/**
 * `내 작업`(`/my/work`) 화면. 역할별 다음 작업 카드와 참여 정보, 프로젝트 나가기를 둔다.
 *
 * 개인 작업공간의 두 화면(`내 작업`·`내 활동`) 중 첫째다.
 * 본문은 `components/MyQueues.tsx`가 역할별로 그린다. `내 활동` 본문은 `components/MyActivity.tsx`다.
 *
 * 역할은 선택한 프로젝트의 `my_role`(["projects"] 캐시)로 정한다.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LogOut, MoreHorizontal } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import { useOutsideClose } from "@/shared/hooks/useOutsideClose";
import { Button } from "@/shared/ui/Button";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { Label } from "@/shared/ui/Label";
import { Modal } from "@/shared/ui/Modal";
import { PageSkeleton } from "@/shared/ui/PageSkeleton";
import { PersonalHeader } from "@/shell/PersonalHeader";
import { apiErrorText } from "@/shared/api/error_text";
import { projectRoleLabel } from "@/lib/role_label";
import { requestProjectSwitcher, useSelectedProject } from "@/store/project";
import { projectKeys, useProjects } from "@/features/projects";

import { fetchReviewWaiting, fetchWorkWaiting, leaveProject, meKeys } from "../api";
import { AdminQueue, LabelerQueue, ReviewerQueue } from "../components/MyQueues";

/** 역할에 맞는 `내 작업` 본문을 고른다. */
export function QueueContent({ role }: { role: string | null }) {
  if (role === "reviewer") return <ReviewerQueue />;
  if (role === "project_admin") return <AdminQueue />;
  return <LabelerQueue />;
}

/**
 * 참여 설정 메뉴를 그린다. 지금은 `프로젝트 나가기` 하나만 있다.
 *
 * 되돌릴 수 없는 동작은 화면에 늘 노출하지 않는다. 메뉴를 열어야 나오고, 메뉴 안에서만 빨간색이다.
 * 늘 보이는 빨간 버튼은 매일 시선을 끈다.
 */
function MembershipMenu({ onLeave }: { onLeave: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutsideClose(open, ref, () => setOpen(false));

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="참여 설정"
        className="flex size-8 items-center justify-center rounded-control text-label-2 transition-colors hover:bg-fill hover:text-label focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
      >
        <MoreHorizontal size={16} aria-hidden="true" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-30 mt-1.5 w-44 origin-top-right animate-pop-in overflow-hidden rounded-card border border-separator bg-raised p-1 shadow-pop"
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onLeave();
            }}
            className="flex w-full items-center gap-2 rounded-control px-2.5 py-1.5 text-left text-footnote text-bad-text transition-colors hover:bg-bad-muted"
          >
            <LogOut size={14} className="shrink-0" aria-hidden="true" />
            프로젝트 나가기
          </button>
        </div>
      )}
    </div>
  );
}

/** 확인 창의 잃는 것 한 줄을 그린다. 이름은 왼쪽, 값은 오른쪽이다. */
function LossRow({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-label-2">{name}</dt>
      <dd className="text-right text-label">{children}</dd>
    </div>
  );
}

/**
 * `내 작업` 페이지를 그린다. 참여 정보(역할·기간·잔여일)는 머리 오른쪽 한 줄에 둔다.
 * 프로젝트 이름은 머리의 프로젝트 칩이 이미 보여 주므로 되풀이하지 않는다.
 */
export function MyWorkPage() {
  const { selectedProjectId } = useSelectedProject();
  const projects = useProjects();
  const project = projects.data?.find((p) => p.project_id === selectedProjectId);
  const myRole = project?.my_role ?? null;

  const navigate = useNavigate();
  const qc = useQueryClient();
  const [leaveOpen, setLeaveOpen] = useState(false);
  const [leaveReason, setLeaveReason] = useState("");

  /*
    나가면 무엇을 잃는지 수로 보여 준다(비가역 액션에는 판단 근거를 같은 화면에 둔다).
    서버는 이 프로젝트에서 보유한 라벨링·검수 작업을 모두 대기열로 돌린다(`release_holds`).
    두 대기열의 보유 수는 대기 수 응답의 `held`이고, 멤버면 역할과 관계없이 받는다.
  */
  const heldWork = useQuery({
    queryKey: meKeys.workWaiting(selectedProjectId),
    queryFn: () => fetchWorkWaiting(selectedProjectId),
    enabled: !!selectedProjectId && leaveOpen,
  });
  const heldReview = useQuery({
    queryKey: meKeys.reviewWaiting(selectedProjectId),
    queryFn: () => fetchReviewWaiting(selectedProjectId),
    enabled: !!selectedProjectId && leaveOpen,
  });
  const myClaimed = { isLoading: heldWork.isLoading || heldReview.isLoading };
  const releasing = (heldWork.data?.held ?? 0) + (heldReview.data?.held ?? 0);

  const leave = useMutation({
    mutationFn: () => leaveProject(project!.project_id, leaveReason.trim() || null),
    onSuccess: () => {
      setLeaveOpen(false);
      setLeaveReason("");
      qc.invalidateQueries({ queryKey: projectKeys.list() });
      navigate("/");
    },
  });

  const membership = project && (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-footnote">
      <span className="font-semibold text-label">{myRole ? projectRoleLabel(myRole) : "멤버"}</span>
      <span aria-hidden="true" className="mx-1 h-4 w-px bg-separator" />
      <MembershipMenu onLeave={() => setLeaveOpen(true)} />
    </div>
  );

  return (
    <div className="space-y-4">
      <PersonalHeader actions={membership || undefined} />

      {/*
        역할별 본문. 프로젝트를 고른 뒤에만 그린다. 전환기는 고른 프로젝트가 없어도 첫 프로젝트를
        자동으로 고르지 않는다. 역할을 확인하기 전에 그리면 라벨링 대기열이 대체 화면으로 뜬다.
      */}
      {projects.isError ? (
        <ErrorState message="프로젝트 조회 실패" onRetry={() => void projects.refetch()} />
      ) : !selectedProjectId ? (
        <EmptyState
          message="프로젝트 선택 필요"
          description="프로젝트를 고르면 그 프로젝트의 내 작업이 보입니다."
          action={<Button variant="secondary" onClick={requestProjectSwitcher}>프로젝트 고르기</Button>}
        />
      ) : projects.isLoading ? (
        <PageSkeleton table />
      ) : !project ? (
        <EmptyState
          message="프로젝트 선택 필요"
          description="고른 프로젝트가 참여 목록에 없습니다."
          action={<Button variant="secondary" onClick={requestProjectSwitcher}>프로젝트 고르기</Button>}
        />
      ) : (
        <QueueContent role={myRole} />
      )}

      {/* 프로젝트 나가기 확인 창. 굵은 결과, 잃는 것의 수, 사유, 취소·나가기 순서로 둔다. */}
      <Modal
        open={leaveOpen}
        onClose={() => setLeaveOpen(false)}
        title="프로젝트 나가기"
        size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={() => setLeaveOpen(false)}>취소</Button>
            <Button variant="danger" disabled={leave.isPending} onClick={() => leave.mutate()}>
              {leave.isPending ? "나가는 중…" : "나가기"}
            </Button>
          </>
        }
      >
        <p className="text-callout font-semibold text-label">{project?.name}에서 나갑니다.</p>
        {/* 보유 수는 받은 뒤에만 쓴다. 받기 전에 0을 쓰면 실제 0건과 구분되지 않는다. */}
        <dl className="mt-3 space-y-1.5 rounded-control bg-canvas-2 px-3.5 py-3 text-footnote">
          <LossRow name="보유한 작업(라벨링·검수)">
            {myClaimed.isLoading ? (
              <span className="text-label-2">확인 중</span>
            ) : releasing > 0 ? (
              <>
                <span className="font-semibold tabular-nums">{releasing}건</span> 대기열로 돌아감
              </>
            ) : (
              "없음"
            )}
          </LossRow>
          {releasing > 0 && <LossRow name="임시저장">지워짐</LossRow>}
          <LossRow name="다시 참여">관리자 초대 필요</LossRow>
        </dl>
        {/* 선택 항목은 필수 표시가 없는 라벨과 입력란 안내 문구로 알린다(`Label`의 `required` 관례). */}
        <Label htmlFor="leave-reason" className="mb-1.5 mt-4 block">
          사유
        </Label>
        <textarea
          id="leave-reason"
          value={leaveReason}
          onChange={(e) => setLeaveReason(e.target.value)}
          rows={3}
          placeholder="선택 입력"
          className="w-full rounded-field border border-separator-strong bg-panel px-3 py-2 text-callout text-label placeholder:text-placeholder focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus/25"
        />
        {/*
          서버가 막는 경우가 하나 있다. 남는 관리자가 없으면 409다.
          주의: `api()`는 `Error`가 아니라 `{ status, detail }`을 던진다.
        */}
        {leave.error &&
          (() => {
            const t =
              (leave.error as { status?: number }).status === 409
                ? { message: "마지막 관리자라서 나갈 수 없습니다.", hint: "다른 멤버를 관리자로 지정한 뒤 다시 시도해주세요." }
                : apiErrorText(leave.error, "프로젝트 나가기 실패");
            return (
              <div role="alert" className="mt-3">
                <p className="text-footnote font-semibold text-bad-text">{t.message}</p>
                {t.hint && <p className="text-footnote text-label-2">{t.hint}</p>}
              </div>
            );
          })()}
      </Modal>
    </div>
  );
}
