/**
 * 멤버 화면(`pages/people.tsx`)의 본문. 읽기 전용 멤버 표와 초대 창을 그린다.
 *
 * 표는 읽기만 한다. 행을 누르면 사람 서랍이 열리고, 역할 편집과 제거는
 * 서랍의 설정 탭 한 곳에서 한다. 한 사람을 고치려고 여러 곳을 열지 않게 한다.
 *
 * 이 화면과 초대·편집은 모두 프로젝트 관리자 권한이다(`authz.require_project_admin`).
 * 멤버는 관리자의 초대로만 들어온다.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { fetchLabelerPerformance, fmtDay, insightsKeys } from "@/features/insights";
import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { DataTable, useTableUrlState, type DataColumn } from "@/shared/ui/DataTable";
import { EmptyState } from "@/shared/ui/EmptyState";
import { Input } from "@/shared/ui/Input";
import { Label } from "@/shared/ui/Label";
import { Modal } from "@/shared/ui/Modal";
import { Select } from "@/shared/ui/Select";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/shared/ui/Toast";
import { WorkerCell } from "@/shared/ui/WorkerCell";
import { type ApiError, type ProjectMemberDTO } from "@/shared/api/client";
import { errorText, type ErrorText } from "@/shared/api/error_text";
import { PROJECT_ROLE_LABEL as ROLE_LABEL, projectRoleLabel } from "@/lib/role_label";

import { inviteMember, listMembers, peopleKeys } from "../api";
import { memberStatusLabel } from "../lib/member_status";
import { ROLE_DESC, ROLES, type ProjectRole } from "../lib/roles";
import { PersonDrawer } from "./PersonDrawer";

/** 422 필드 오류. `api()`가 Pydantic 오류를 `fields: [{path, message}]`로 바꿔 던진다. */
type FieldApiError = ApiError & { fields?: { path: string; message: string }[] };

const PAGE_SIZE = 20;

/**
 * 머리 사실 줄을 만든다. `활성 11명 · 나감 1명`처럼 0이 아닌 상태만 센다.
 * 목록은 서버가 페이지 없이 모두 주므로 이 수가 전체다.
 */
export function memberFacts(rows: readonly ProjectMemberDTO[]): string {
  const count: Record<string, number> = {};
  for (const m of rows) count[m.status] = (count[m.status] ?? 0) + 1;
  return ["active", "suspended", "left", "pending", "rejected"]
    .filter((s) => count[s])
    .map((s) => `${memberStatusLabel(s)} ${count[s].toLocaleString("ko-KR")}명`)
    .join(" · ");
}

/** 최근 활동 열의 글자를 만든다. 오늘이면 `오늘`, 아니면 `9월 25일`이다. */
function lastActivityText(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const now = new Date();
  return d.toDateString() === now.toDateString() ? "오늘" : fmtDay(iso);
}

/**
 * 멤버 표와 사람 서랍, 초대 창을 그린다.
 *
 * Args:
 *   inviteOpen / onInviteOpenChange: 초대 창 상태. 여는 버튼은 페이지 머리에 있다.
 */
export function MembersPanel({
  pid,
  inviteOpen,
  onInviteOpenChange,
}: {
  pid: string;
  inviteOpen: boolean;
  onInviteOpenChange: (open: boolean) => void;
}) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [openUser, setOpenUser] = useState<string | null>(null);
  // 표의 검색·정렬·페이지를 주소에 둔다. 새로고침해도 같은 목록이다.
  const tableUrl = useTableUrlState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<ProjectRole>("annotator");
  // 초대 실패는 모두 방금 입력한 이메일의 문제라서 창 안 입력란 아래에 둔다.
  const [inviteError, setInviteError] = useState<ErrorText | null>(null);

  const members = useQuery({
    queryKey: peopleKeys.members(pid),
    queryFn: () => listMembers(pid),
    enabled: !!pid,
  });
  /*
    보유 중·최근 활동은 멤버 응답에 없다. 통계 사람 표와 같은 조회(전체 기간)에서 읽는다.
    같은 쿼리 키라 사람 서랍과 통계 화면이 캐시를 함께 쓴다.
  */
  const perf = useQuery({
    queryKey: insightsKeys.labelerPerf(pid, "all"),
    queryFn: () => fetchLabelerPerformance(pid!),
    enabled: !!pid,
  });
  const perfOf = new Map((perf.data ?? []).map((r) => [r.user_id, r]));

  const invalidate = () => qc.invalidateQueries({ queryKey: peopleKeys.members(pid) });
  const inviteMut = useMutation({
    mutationFn: (v: { email: string; role: string }) => inviteMember(pid, v),
    onSuccess: (m) => {
      onInviteOpenChange(false);
      setInviteEmail("");
      setInviteRole("annotator");
      setInviteError(null);
      toast("success", "초대했습니다", `${m.display_name ?? m.email ?? ""} · ${projectRoleLabel(m.role)}`);
      void invalidate();
    },
    /*
      흔한 실패는 셋이다. 없는 계정(404), 이미 멤버(409), 쓸 수 없는 계정(409).
      형식이 틀린 이메일은 서버 검증(422)이 `email` 필드를 지정해 알려 준다.
    */
    onError: (e: FieldApiError) => {
      if (e?.fields?.some((f) => f.path.split(".").pop() === "email"))
        setInviteError({ message: "이메일 형식 오류", hint: "user@example.com 형식으로 입력해주세요." });
      else if (e?.status === 404)
        setInviteError({ message: "없는 계정", hint: "최고관리자가 만든 계정의 이메일만 초대합니다." });
      else setInviteError(errorText(e?.detail, { message: "초대 실패", hint: "잠시 후 다시 시도해주세요." }));
    },
  });

  const rows = members.data ?? [];
  const nameOf = (m: ProjectMemberDTO) => m.display_name ?? m.email ?? "이름 없음";

  const columns: DataColumn<ProjectMemberDTO>[] = [
    {
      label: "멤버",
      kind: "person",
      value: (m) => `${m.display_name ?? ""} ${m.email ?? ""}`.trim(),
      cell: (m) => <WorkerCell name={nameOf(m)} email={m.email} />,
    },
    {
      label: "역할",
      kind: "select",
      value: (m) => projectRoleLabel(m.role),
      cell: (m) => <span className="text-body text-label">{projectRoleLabel(m.role)}</span>,
    },
    {
      label: "상태",
      kind: "status",
      value: (m) => memberStatusLabel(m.status),
      cell: (m) => <StatusBadge status={m.status} label={memberStatusLabel(m.status)} />,
    },
    {
      /*
        지금 보유 중인 작업 수(라벨링과 검수의 합)다. 한 사람은 역할마다 한 건씩 맡고 한도는 없다.
        활성 멤버가 아니거나 실적 행이 없으면 `—`다.
      */
      label: "보유 중",
      // 머리 아이콘을 다른 열과 같이 싣는다. 한 표 안에서 머리 모양이 열마다 다르지 않게 한다.
      kind: "number",
      align: "right",
      searchText: false,
      value: (m) => perfOf.get(m.user_id)?.in_flight ?? null,
      cell: (m) => {
        const p = perfOf.get(m.user_id);
        if (m.status !== "active" || p == null)
          return <span className="text-footnote text-label-3">—</span>;
        return <span className="text-body tabular-nums text-label">{p.in_flight.toLocaleString("ko-KR")}</span>;
      },
    },
    {
      label: "최근 활동",
      kind: "date",
      align: "right",
      searchText: false,
      value: (m) => perfOf.get(m.user_id)?.last_activity ?? null,
      cell: (m) => (
        <span className="text-footnote tabular-nums text-label-2">
          {lastActivityText(perfOf.get(m.user_id)?.last_activity)}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-5">
      <Card>
        <DataTable<ProjectMemberDTO>
          bare
          unit="명"
          columns={columns}
          rows={rows}
          rowKey={(m) => m.user_id}
          rowLabel={(m) => nameOf(m)}
          onRowClick={(m) => setOpenUser(m.user_id)}
          rowClassName={(m) => (m.user_id === openUser ? "bg-amber-soft" : "")}
          loading={members.isLoading}
          error={members.isError}
          onRetry={() => void members.refetch()}
          search="이름·이메일 검색"
          pageSize={PAGE_SIZE}
          urlState={tableUrl}
          empty={
            <EmptyState
              message="멤버 없음"
              action={
                <Button size="sm" onClick={() => { setInviteError(null); onInviteOpenChange(true); }}>
                  멤버 초대
                </Button>
              }
            />
          }
        />
      </Card>

      <PersonDrawer
        pid={pid}
        userId={openUser}
        onClose={() => setOpenUser(null)}
        initialTab="settings"
      />

      {/* 멤버 초대. 여는 버튼과 창 제목이 같은 이름이고, 제출 버튼은 동사 하나다. */}
      <Modal
        open={inviteOpen}
        onClose={() => onInviteOpenChange(false)}
        title="멤버 초대"
        size="md"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => onInviteOpenChange(false)}>
              취소
            </Button>
            <Button
              size="sm"
              disabled={!inviteEmail.trim() || inviteMut.isPending}
              onClick={() => inviteMut.mutate({ email: inviteEmail.trim(), role: inviteRole })}
            >
              {inviteMut.isPending ? "초대 중…" : "초대"}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <div>
            <Label htmlFor="invite-email" className="mb-1">이메일</Label>
            <Input
              id="invite-email"
              type="email"
              placeholder="user@example.com"
              aria-invalid={inviteError ? true : undefined}
              aria-describedby={inviteError ? "invite-error invite-email-help" : "invite-email-help"}
              value={inviteEmail}
              onChange={(e) => { setInviteEmail(e.target.value); setInviteError(null); }}
            />
            {/*
              초대는 이미 있는 계정을 프로젝트에 넣는 일이다. 가입 절차가 없으므로 없는 이메일은 거절된다(`invitee_unavailable`).
              누른 뒤에야 알지 않도록 입력란 아래에 먼저 쓴다.
            */}
            <p id="invite-email-help" className="mt-1 text-caption text-label-3">
              이미 등록된 계정만 초대할 수 있습니다. 계정은 최고관리자가 플랫폼 관리의 사용자에서 만듭니다.
            </p>
            {/* 결과(굵게)와 안내(작게)를 두 줄로 나눈다. */}
            {inviteError && (
              <div id="invite-error" role="alert" className="mt-1.5">
                <p className="text-footnote font-semibold text-bad-text">{inviteError.message}</p>
                {inviteError.hint && <p className="text-caption text-label-3">{inviteError.hint}</p>}
              </div>
            )}
          </div>
          <div>
            <Label htmlFor="invite-role" className="mb-1">역할</Label>
            <Select
              id="invite-role"
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value as ProjectRole)}
              aria-describedby="invite-role-desc"
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABEL[r]}
                </option>
              ))}
            </Select>
            <p id="invite-role-desc" className="mt-1 text-caption text-label-3">{ROLE_DESC[inviteRole]}</p>
          </div>
          {/* `invite_member`는 `status="active"`로 넣는다. */}
          <p className="text-footnote text-label-2">초대하면 바로 활성 멤버가 됩니다.</p>
        </div>
      </Modal>
    </div>
  );
}
