/**
 * 사람 서랍의 `설정` 탭. 역할을 바꾸고 멤버를 제거하거나 다시 초대한다.
 *
 * 멤버 표는 읽기 전용이고 편집은 이 탭 한 곳에서 한다. 한 사람을 고치려고 여러 곳을 열지 않게 한다.
 * 동시 보유 한도는 없다. 한 사람은 역할마다 한 건씩 맡는다.
 * 서랍은 관리자 화면에서만 열리므로 서버 가드(`require_project_admin`)를 통과한 사람만 이 탭을 본다.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { insightsKeys } from "@/features/insights";
import { projectKeys } from "@/features/projects";
import { Button } from "@/shared/ui/Button";
import { Label } from "@/shared/ui/Label";
import { Modal } from "@/shared/ui/Modal";
import { Select } from "@/shared/ui/Select";
import { useToast } from "@/shared/ui/Toast";
import { failToast } from "@/shared/ui/failToast";
import { type ApiError, type ProjectMemberDTO } from "@/shared/api/client";
import { eulReul, withJosa } from "@/shared/lib/josa";
import { PROJECT_ROLE_LABEL as ROLE_LABEL, projectRoleLabel } from "@/lib/role_label";
import { useAuth } from "@/store/auth";

import {
  inviteMember,
  peopleKeys,
  removeMember,
  updateMemberRole,
} from "../api";
import { memberStatusLabel } from "../lib/member_status";
import { ROLE_DESC, ROLES, type ProjectRole } from "../lib/roles";
import { FactBox } from "./FactBox";

/** 폼 값. */
interface Form {
  role: string;
}

/** 멤버의 저장된 값을 폼 값으로 옮긴다. */
function formOf(m: ProjectMemberDTO): Form {
  return { role: m.role };
}

/** 바뀐 항목 이름을 돌려준다. 저장 막대가 `바뀐 항목 1개 · 역할`로 쓴다. */
const FIELD_NAME: Record<keyof Form, string> = {
  role: "역할",
};

/** 바뀐 입력란 표시. 호박색 점 하나로 알리고 스크린리더에는 글자로 읽힌다. */
function ChangedDot({ on }: { on: boolean }) {
  if (!on) return null;
  return (
    <>
      <span className="ml-1.5 inline-block size-1.5 rounded-capsule bg-amber align-middle" aria-hidden="true" />
      <span className="sr-only">바뀜</span>
    </>
  );
}

/**
 * 설정 탭을 그린다.
 *
 * Args:
 *   holding: 지금 보유 중인 작업 수(라벨링과 검수의 합). 역할 변경 확인 창이 실제 수로 알린다. 받기 전이면 `null`.
 *   onDirtyChange: 저장하지 않은 변경이 생기거나 없어질 때 부른다. 서랍이 닫기 전 확인에 쓴다.
 *   onGone: 멤버를 제거했을 때 부른다. 서랍을 닫는다.
 */
export function PersonSettings({
  pid,
  member,
  holding,
  onDirtyChange,
  onGone,
}: {
  pid: string;
  member: ProjectMemberDTO;
  holding: number | null;
  onDirtyChange: (dirty: boolean) => void;
  onGone: () => void;
}) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const saved = useMemo(() => formOf(member), [member]);
  const [form, setForm] = useState<Form>(saved);
  // 서버 값이 바뀌면(저장 뒤 다시 받기) 폼을 그 값으로 맞춘다.
  useEffect(() => setForm(saved), [saved]);

  const effective = (Object.keys(FIELD_NAME) as (keyof Form)[]).filter((k) => form[k] !== saved[k]);
  const dirty = effective.length > 0;
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);

  const [confirmRole, setConfirmRole] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);

  /*
    편집하는 사람 자신의 행이면 역할 변경이 곧 본인 권한 변경이다. 관리자가 자기 역할을 내리면 설정·멤버 관리 권한을 잃는다.
    최고관리자는 프로젝트 역할과 무관하게 관리하므로 해당하지 않는다.
  */
  const me = useAuth((s) => s.user);
  const selfDemote =
    member.user_id === me?.user_id && me?.global_role !== "super_admin"
    && saved.role === "project_admin" && form.role !== "project_admin";

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: peopleKeys.members(pid) });
    void qc.invalidateQueries({ queryKey: insightsKeys.labelerPerfAll(pid) });
    // 편집한 사람 본인의 역할이 바뀌었을 수 있다. 화면 권한은 프로젝트 목록의 `my_role`에서 나오므로 함께 새로 받는다.
    if (member.user_id === me?.user_id) void qc.invalidateQueries({ queryKey: projectKeys.list() });
  };

  /* 역할만 저장한다. 실패하면 원인을 알리고 서버 값으로 다시 맞춘다. */
  const save = useMutation({
    mutationFn: () => updateMemberRole(pid, member.user_id, form.role),
    onSuccess: () => {
      toast("success", "설정을 저장했습니다");
      invalidate();
    },
    onError: (e) => {
      failToast(toast, e, "설정 저장 실패");
      invalidate();
    },
  });

  const remove = useMutation({
    mutationFn: () => removeMember(pid, member.user_id),
    onSuccess: () => {
      toast("success", "멤버를 제거했습니다");
      invalidate();
      onGone();
    },
    onError: (e) => {
      const err = e as unknown as ApiError;
      if (err?.status === 409)
        toast("error", "제거할 수 없습니다", "이 프로젝트의 마지막 관리자입니다. 다른 관리자를 먼저 지정하세요.");
      else failToast(toast, e, "제거 실패");
      invalidate();
    },
  });

  const reinvite = useMutation({
    mutationFn: () => inviteMember(pid, { email: member.email, role: member.role }),
    onSuccess: () => {
      toast("success", "다시 초대했습니다");
      invalidate();
    },
    onError: (e) => failToast(toast, e, "초대 실패"),
  });

  const name = member.display_name ?? member.email ?? "이 멤버";
  const editable = member.status === "active";
  const holdingText = holding == null ? "—" : `${holding.toLocaleString("ko-KR")}건`;

  const onSave = () => {
    if (form.role !== saved.role) setConfirmRole(true);
    else save.mutate();
  };

  return (
    <div className="space-y-6 pb-2">
      {member.status === "active" && (
        <section className="space-y-4">
          <h3 className="text-body font-semibold text-label">참여 설정</h3>
          {editable ? (
            <>
              <div>
                <Label htmlFor="person-role" className="mb-1">
                  역할
                  <ChangedDot on={form.role !== saved.role} />
                </Label>
                <Select
                  id="person-role"
                  value={form.role}
                  onChange={(e) => setForm({ ...form, role: e.target.value })}
                  aria-describedby="person-role-desc"
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {ROLE_LABEL[r]}
                    </option>
                  ))}
                </Select>
                <p id="person-role-desc" className="mt-1 text-caption text-label-3">
                  {ROLE_DESC[form.role as ProjectRole] ?? ""}
                </p>
              </div>
            </>
          ) : (
            <FactBox facts={[{ label: "역할", value: projectRoleLabel(member.role) }]} />
          )}
        </section>
      )}

      {member.status === "active" && (
        <section className="flex items-center justify-between gap-4 rounded-card border border-separator px-4 py-3.5">
          <div className="min-w-0">
            <h3 className="text-body font-semibold text-label">프로젝트에서 제거</h3>
            {/* 서버(`work.service.release_holds`)는 보유한 라벨링·검수 작업을 모두 대기열로 돌린다. */}
            <p className="mt-0.5 text-footnote text-label-3">
              {holding ? "보유 중인 라벨링·검수 작업은 대기열로 돌아갑니다." : "보유 중인 작업이 없습니다."}
            </p>
          </div>
          <Button size="sm" variant="danger-outline" disabled={remove.isPending} onClick={() => setConfirmRemove(true)}>
            제거
          </Button>
        </section>
      )}

      {/* 승인 대기 행은 참여 요청이 있던 데이터에만 남는다. 승인 경로가 없으므로 다시 초대로 들인다. */}
      {(member.status === "left" || member.status === "rejected" || member.status === "pending") && (
        <section className="flex items-center justify-between gap-4 rounded-card border border-separator px-4 py-3.5">
          <div className="min-w-0">
            <h3 className="text-body font-semibold text-label">{memberStatusLabel(member.status)} 멤버</h3>
            <p className="mt-0.5 text-footnote text-label-3">작업과 판정 기록은 남습니다.</p>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button size="sm" variant="secondary" disabled={reinvite.isPending || !member.email} onClick={() => reinvite.mutate()}>
              다시 초대
            </Button>
            <Button size="sm" variant="danger-outline" disabled={remove.isPending} onClick={() => setConfirmRemove(true)}>
              목록에서 제거
            </Button>
          </div>
        </section>
      )}

      {/* 저장 막대는 바뀐 항목이 있을 때만 흑연색 막대로 띄운다. 서랍 본문 바닥에 붙는다. */}
      {editable && dirty && (
        <div
          data-surface="graphite"
          className="sticky bottom-0 -mx-6 -mb-5 flex items-center gap-3 bg-panel px-6 py-3.5"
        >
          <p className="min-w-0 flex-1 truncate text-footnote font-semibold text-label">
            바뀐 항목 {effective.length}개 · {effective.map((k) => FIELD_NAME[k]).join(", ")}
          </p>
          <Button size="sm" variant="secondary" disabled={save.isPending} onClick={() => setForm(saved)}>
            되돌리기
          </Button>
          <Button size="sm" disabled={save.isPending} onClick={onSave}>
            {save.isPending ? "저장 중…" : "저장"}
          </Button>
        </div>
      )}

      <Modal
        open={confirmRole}
        onClose={() => setConfirmRole(false)}
        title="역할 변경"
        size="sm"
        icon={selfDemote ? <ShieldAlert size={16} aria-hidden="true" /> : undefined}
        tone={selfDemote ? "warn" : undefined}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setConfirmRole(false)}>취소</Button>
            <Button size="sm" onClick={() => { setConfirmRole(false); save.mutate(); }}>
              {selfDemote ? "내 역할 변경" : "변경"}
            </Button>
          </>
        }
      >
        {/* 자기 강등은 되돌리려면 다른 관리자가 필요하다. 결과(굵게)와 되돌리는 법(작게)을 먼저 둔다. */}
        {selfDemote && (
          <div className="mb-3 rounded-card bg-warn-muted px-3.5 py-3">
            <p className="text-footnote font-semibold text-warn-text">설정·멤버 관리 권한을 잃습니다</p>
            <p className="mt-0.5 text-caption text-label-2">되돌리려면 다른 관리자나 최고관리자가 역할을 바꿔야 합니다.</p>
          </div>
        )}
        {/* 서버(`update_member_role`)는 역할만 바꾸고 보유 작업을 풀지 않는다. 바뀌면 이 안내도 고친다. */}
        <p className="text-body text-label">
          {name}의 역할을 {projectRoleLabel(saved.role)}에서 {withJosa(projectRoleLabel(form.role), "으로", "로")} 바꿉니다.
        </p>
        <div className="mt-3">
          <FactBox
            facts={[
              { label: "적용", value: "바로" },
              { label: "보유 중인 작업", value: holding == null ? "그대로" : `${holdingText} 그대로` },
            ]}
          />
        </div>
      </Modal>

      <Modal
        open={confirmRemove}
        onClose={() => setConfirmRemove(false)}
        title={member.status === "active" ? "멤버 제거" : "목록에서 제거"}
        size="sm"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setConfirmRemove(false)}>취소</Button>
            <Button
              variant="danger"
              size="sm"
              disabled={remove.isPending}
              onClick={() => { setConfirmRemove(false); remove.mutate(); }}
            >
              제거
            </Button>
          </>
        }
      >
        {member.status === "active" ? (
          <>
            <p className="text-body text-label">{eulReul(name)} 프로젝트에서 제거합니다.</p>
            <div className="mt-3">
              <FactBox
                facts={[
                  { label: "보유 중인 작업", value: holding ? `${holdingText} → 대기열(라벨링·검수)` : "없음" },
                  { label: "작성하던 내용", value: "지워짐" },
                ]}
              />
            </div>
            <p className="mt-3 text-footnote text-label-3">다시 넣으려면 멤버 초대로 넣습니다.</p>
          </>
        ) : (
          <>
            <p className="text-body text-label">{eulReul(name)} 멤버 목록에서 뺍니다.</p>
            <div className="mt-3">
              <FactBox facts={[{ label: "상태", value: memberStatusLabel(member.status) }]} />
            </div>
            <p className="mt-3 text-footnote text-label-3">작업과 판정 기록은 남습니다.</p>
          </>
        )}
      </Modal>
    </div>
  );
}
