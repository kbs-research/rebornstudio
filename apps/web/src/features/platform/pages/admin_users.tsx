/**
 * 사용자 관리 화면(`/admin/users`). super_admin 전용이다.
 *
 * 공개 가입이 없어 계정은 이 화면에서 만든다. 계정 만들기는 POST /admin/users를 쓰고,
 * 응답의 1회용 재설정 링크를 사용자에게 전달하면 사용자가 그 링크로 비밀번호를 정한다.
 * 표 하나에서 역할 변경·정지·재설정 링크 발급·활동 이력을 다룬다. 목록은 GET /users를 쓴다.
 * 플랫폼 역할 변경과 정지·해제는 PATCH /admin/users/:id/role|status를 쓴다.
 * 비밀번호 재설정 링크 발급은 POST /admin/users/:id/password-reset을 쓴다.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import {
  ArrowRight, Ban, Check, Copy, History, KeyRound, Link2, Ellipsis, ShieldAlert,
  TriangleAlert, UserPlus,
} from "lucide-react";

import { Avatar } from "@/shared/ui/Avatar";
import { Button } from "@/shared/ui/Button";
import { DataTable, useTableUrlState, type DataColumn } from "@/shared/ui/DataTable";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { FIELD_BASE } from "@/shared/ui/field";
import { Input } from "@/shared/ui/Input";
import { Label } from "@/shared/ui/Label";
import { Modal } from "@/shared/ui/Modal";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Select } from "@/shared/ui/Select";
import { SkeletonText } from "@/shared/ui/Skeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/shared/ui/Toast";
import { useOutsideClose } from "@/shared/hooks/useOutsideClose";
import type { UserDTO } from "@/shared/api/client";
import { cn } from "@/shared/lib/cn";
import { copyText } from "@/shared/lib/clipboard";
import { apiErrorText } from "@/shared/api/error_text";
import { fmtEventTime, humanizeEvent } from "@/features/audit";
import { useProjects } from "@/features/projects";
import { withJosa } from "@/shared/lib/josa";
import { GLOBAL_ROLES, globalRoleLabel } from "@/lib/role_label";
import { fmtDate } from "@/shared/lib/time";
import { useAuth } from "@/store/auth";

import {
  createUser,
  fetchAllUsers,
  fetchUserActivity,
  issuePasswordReset,
  platformKeys,
  setUserRole,
  setUserStatus,
  type ResetIssued,
} from "../api";

type GlobalRole = "user" | "super_admin";

/**
 * 플랫폼 역할마다 할 수 있는 일. 역할 변경 확인 창이 쓴다.
 * 서버 판정과 같은 내용이다. 프로젝트 추가와 삭제는 최고관리자만 한다(`services/org/project.py`).
 */
const ROLE_SCOPE: Record<GlobalRole, string> = {
  user: "참여한 프로젝트에서만 일합니다.",
  super_admin: "프로젝트를 추가하고 모든 프로젝트와 플랫폼 설정을 관리합니다.",
};

/**
 * 활동 이력 조회 상한. 상한에 닿으면 화면이 목록이 잘렸다고 알린다.
 * 화면 안내도 이 수를 그대로 쓴다. 상한과 안내가 다르면 틀린 안내가 된다.
 */
const ACTIVITY_LIMIT = 100;

/** 날짜만 `9월 25일`로 적는다(`shared/lib/time`의 날짜 규칙). 값이 없으면 대시다. */
const ymd = (iso: string | null | undefined) => (iso ? fmtDate(iso) : "—");

/**
 * 계정 상태 이름. 배지와 정렬 순서가 같은 목록을 쓴다.
 * 작업 상태 사전(`StatusBadge`)의 `대기`·`반려`는 작업 상태 이름이라 계정에 쓰지 않는다.
 */
const ACCOUNT_STATUS: [string, string][] = [
  ["active", "활성"],
  ["suspended", "정지"],
];
const ACCOUNT_STATUS_LABEL: Record<string, string> = Object.fromEntries(ACCOUNT_STATUS);
/** 계정 상태의 정렬 순서. 표시 순서와 같다. */
const ACCOUNT_STATUS_RANK: Record<string, number> = Object.fromEntries(ACCOUNT_STATUS.map(([k], i) => [k, i]));
/** 플랫폼 역할의 정렬 순서. 권한이 넓은 쪽이 뒤다(`GLOBAL_ROLES`와 같은 순서). */
const ROLE_RANK: Record<string, number> = Object.fromEntries(GLOBAL_ROLES.map((r, i) => [r, i]));

/** 확인 창이 다루는 사람. 창 머리에 이름과 이메일을 같이 적는다. */
type Target = { uid: string; name: string; email: string };

/** 창 제목 아래에 대상을 적는 한 줄을 그린다. 어느 대상의 창인지 본문을 읽기 전에 알 수 있다. */
function ModalTarget({ children }: { children: ReactNode }) {
  return <p className="-mt-2 mb-4 text-footnote text-label-3">{children}</p>;
}

/** 사용자 관리 화면을 그린다. 계정 만들기, 역할 변경, 정지, 재설정 링크 발급, 활동 이력을 다룬다. */
export function AdminUsersPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  // 자기 행에는 쓸 수 없는 컨트롤을 그리지 않는다. 서버도 자기 역할 변경과 정지를 403으로 막는다.
  const myId = useAuth((s) => s.user?.user_id);
  const allTable = useTableUrlState("");
  /** 실패 토스트를 띄우는 핸들러를 만든다. 오류 사전이 사유를 구분한다(409 이미 처리됨, 403 자기 계정 등). */
  const fail = (fallback: string) => (e: unknown) => {
    const t = apiErrorText(e, fallback);
    toast("error", t.message, t.hint);
  };

  const all = useQuery({
    queryKey: platformKeys.allUsers(),
    queryFn: fetchAllUsers,
  });

  /*
    계정 만들기. 서버가 활성 계정을 만들고 1회용 재설정 링크 토큰을 돌려준다.
    링크는 이 응답에서만 볼 수 있어서 결과를 재설정 링크 창으로 바로 보여 준다.
  */
  const [createOpen, setCreateOpen] = useState(false);
  const [draft, setDraft] = useState({ email: "", display_name: "", global_role: "user" as GlobalRole });
  const closeCreate = () => { setCreateOpen(false); setDraft({ email: "", display_name: "", global_role: "user" }); };
  const createMut = useMutation({
    mutationFn: () => createUser({ ...draft, email: draft.email.trim(), display_name: draft.display_name.trim() }),
    onSuccess: (r) => {
      invalidateAll(qc);
      closeCreate();
      setIssued({ ...r.reset, name: r.user.display_name || r.user.email, created: true });
    },
    onError: fail("계정 만들기 실패"),
  });
  const canCreate = draft.email.trim() !== "" && draft.display_name.trim() !== "";

  // 역할 변경
  const [roleChange, setRoleChange] = useState<(Target & { from: GlobalRole; to: GlobalRole }) | null>(null);
  const roleMut = useMutation({
    mutationFn: (v: { uid: string; role: GlobalRole }) => setUserRole(v.uid, v.role),
    onSuccess: () => { invalidateAll(qc); toast("success", "플랫폼 역할을 바꿨습니다"); },
    onError: fail("역할 변경 실패"),
  });

  // 정지·해제
  const [suspendTarget, setSuspendTarget] = useState<Target | null>(null);
  const [unsuspendTarget, setUnsuspendTarget] = useState<Target | null>(null);
  const statusMut = useMutation({
    mutationFn: (v: { uid: string; status: "active" | "suspended" }) => setUserStatus(v.uid, v.status),
    onSuccess: (_u, v) => {
      invalidateAll(qc);
      toast("success", v.status === "suspended" ? "사용자를 정지했습니다" : "정지를 해제했습니다");
    },
    onError: fail("상태 변경 실패"),
  });

  /*
    비밀번호 재설정 링크 발급. 메일 발송 기능이 없어 최고관리자가 링크를 만들어 본인에게 전한다.
    링크는 이 응답에서만 볼 수 있다. 서버가 해시만 저장하므로 다시 보여 줄 수 없다.
  */
  const [resetTarget, setResetTarget] = useState<Target | null>(null);
  const [issued, setIssued] = useState<(ResetIssued & { name: string; created?: boolean }) | null>(null);
  const resetMut = useMutation({
    mutationFn: (t: Target) =>
      issuePasswordReset(t.uid).then((r) => ({ ...r, name: t.name })),
    onSuccess: (r) => { setResetTarget(null); setIssued(r); },
    onError: (e) => { setResetTarget(null); fail("재설정 링크 발급 실패")(e); },
  });

  // 활동 이력. 행 메뉴의 `활동 이력`을 누르면 창에서 조회한다.
  const [activityUser, setActivityUser] = useState<Target | null>(null);
  const activity = useQuery({
    queryKey: platformKeys.userActivity(activityUser?.uid),
    queryFn: () => fetchUserActivity(activityUser!.uid, ACTIVITY_LIMIT),
    enabled: !!activityUser,
    meta: { handled: true }, // 실패는 창이 직접 그린다.
  });
  /*
    활동이 어느 프로젝트의 일인지 이름으로 보여 준다. 최고관리자의 프로젝트 목록은 모든 프로젝트를 싣고,
    셸과 같은 캐시를 쓴다. 이 목록은 삭제된 프로젝트를 싣지 않는다.
  */
  const projects = useProjects({ enabled: !!activityUser });
  const projectName = new Map((projects.data ?? []).map((p) => [p.project_id, p.name]));
  const projectOf = (id: string | null | undefined): { text: string; quiet: boolean } => {
    if (!id) return { text: "플랫폼", quiet: true };
    const n = projectName.get(id);
    return n ? { text: n, quiet: false } : { text: projects.data ? "삭제된 프로젝트" : "", quiet: true };
  };

  const targetOf = (u: UserDTO): Target => ({ uid: u.user_id, name: u.display_name || u.email, email: u.email });

  /** 이름과 이메일을 한 셀 두 줄로 쓴다. 열을 줄여 행마다 동작 버튼이 화면 안에 남게 한다. */
  /*
    이름·이메일 열. 표는 열 폭을 내용으로 정하므로 `truncate`만으로는 말줄임이 되지 않는다.
    폭 상한을 두지 않으면 100자 이름 하나가 열을 늘려 동작 열이 표 밖으로 밀린다.
    잘린 전문은 `title`로 본다.
  */
  const personCell = (u: UserDTO, avatar = false) => (
    <div className="flex items-center gap-3">
      {avatar && <Avatar name={u.display_name || u.email} size={30} />}
      <div className="min-w-0 max-w-[16rem]">
        <p className="truncate font-semibold text-label" title={u.display_name || u.email}>{u.display_name || u.email}</p>
        <p className="truncate text-caption text-label-3" title={u.email}>{u.email}</p>
      </div>
    </div>
  );

  const allColumns: DataColumn<UserDTO>[] = [
    /*
      검색창은 이름과 이메일만 본다(`이름 · 이메일 검색`). 이름이 있는 사람도 이메일로 찾히게 둘을 함께 준다.
      나머지 열은 `searchText: false`다. 역할·상태 코드(`super_admin`·`active`)나 날짜 원문에 걸리지 않는다.
    */
    {
      label: "이름", kind: "title", value: (u) => u.display_name || u.email,
      searchText: (u) => `${u.display_name ?? ""} ${u.email}`, cell: (u) => personCell(u),
    },
    {
      // 플랫폼 전체에 걸친 역할이다. 프로젝트 역할과 구분하려고 `플랫폼 역할`이라 부른다.
      label: "플랫폼 역할",
      kind: "select",
      // 정렬은 권한 순서로 한다. 코드 글자 순(`super_admin`<`user`)은 화면 글자와 맞지 않는다.
      value: (u) => ROLE_RANK[u.global_role] ?? null,
      searchText: false,
      cell: (u) =>
        u.approval_status === "active" && u.user_id !== myId ? (
          // `Select`의 겉 상자는 폭을 다 차지한다. 감싸서 선택 목록 폭만큼만 두고 화살표를 글자 옆에 붙인다.
          <span className="inline-block">
          <Select
            value={u.global_role}
            disabled={roleMut.isPending}
            // 행마다 같은 셀렉트라 누구의 역할인지 이름으로 붙인다.
            aria-label={`${u.display_name ?? u.email} 플랫폼 역할`}
            onChange={(e: React.ChangeEvent<HTMLSelectElement>) =>
              setRoleChange({ ...targetOf(u), from: u.global_role as GlobalRole, to: e.target.value as GlobalRole })
            }
            // 평소에는 글자와 화살표만 보인다. 행마다 테두리 상자가 쌓이면 표가 입력 폼처럼 읽힌다.
            // 가리키거나 포커스하면 테두리가 생겨 바꿀 수 있는 값이라는 것이 드러난다.
            className="h-8 w-auto border-transparent bg-transparent py-0 text-footnote hover:border-separator-strong hover:bg-panel focus:bg-panel"
          >
            {GLOBAL_ROLES.map((r) => <option key={r} value={r}>{globalRoleLabel(r)}</option>)}
          </Select>
          </span>
        ) : (
          // 선택 상자의 글자와 같은 줄에 서도록 왼쪽 안쪽 여백(pl-3)을 맞춘다.
          <span className="pl-3 text-label-2">{globalRoleLabel(u.global_role)}</span>
        ),
    },
    {
      label: "상태",
      kind: "status",
      value: (u) => ACCOUNT_STATUS_RANK[u.approval_status] ?? null,
      searchText: false,
      cell: (u) => <StatusBadge status={u.approval_status} label={ACCOUNT_STATUS_LABEL[u.approval_status]} />,
    },
    { label: "생성일", tone: "muted", kind: "date", value: (u) => u.created_at ?? "", searchText: false, cell: (u) => <span className="tabular-nums">{ymd(u.created_at)}</span> },
    {
      /*
        행 끝에는 그 상태에서 자주 하는 동작만 글자 버튼으로 둔다(정지: 정지 해제).
        드문 활동 이력·재설정 링크·정지는 `⋯` 메뉴에 둔다. 정지는 메뉴 안 구분선 아래 위험 색이다.
        로그인한 사용자 본인의 행에는 역할과 상태를 스스로 바꾸지 않도록 동작 대신 `나`를 적는다.
      */
      label: "",
      sortable: false,
      align: "right",
      cell: (u) => {
        if (u.user_id === myId) return <span className="pr-2 text-caption text-label-3">나</span>;
        const t = targetOf(u);
        if (u.approval_status === "suspended") {
          return (
            <Button type="button" variant="secondary" size="sm" disabled={statusMut.isPending}
              onClick={() => setUnsuspendTarget(t)}
              aria-label={`${t.name} 정지 해제`}>
              정지 해제
            </Button>
          );
        }
        return (
          <UserRowMenu
            label={`${t.name} 관리 메뉴`}
            items={[
              { key: "activity", label: "활동 이력", icon: <History size={14} aria-hidden="true" />, onSelect: () => setActivityUser(t) },
              /*
                재설정 링크는 로그인할 수 있는 계정에만 발급한다(서버도 409로 막는다).
                자기 행에는 두지 않는다. 지금 로그인해 있으므로 계정 화면에서 바꾸면 된다.
              */
              {
                key: "reset", label: "비밀번호 재설정 링크 발급", icon: <Link2 size={14} aria-hidden="true" />,
                disabled: resetMut.isPending, onSelect: () => setResetTarget(t),
              },
              {
                key: "suspend", label: "정지", icon: <Ban size={14} aria-hidden="true" />, danger: true,
                disabled: statusMut.isPending, onSelect: () => setSuspendTarget(t),
              },
            ]}
          />
        );
      },
    },
  ];

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="사용자"
        actions={
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <UserPlus size={14} aria-hidden="true" />
            계정 만들기
          </Button>
        }
      />

      <DataTable<UserDTO>
        unit="명"
        columns={allColumns}
        rows={all.data ?? []}
        rowKey={(u) => u.user_id}
        loading={all.isLoading}
        error={all.isError}
        onRetry={() => void all.refetch()}
        search="이름 · 이메일 검색"
        initialSort={{ i: 3, dir: "desc" }}
        urlState={allTable}
        empty={<EmptyState message="사용자 없음" />}
      />

      {/* 계정 만들기 창. 비밀번호는 받지 않는다. 다음 창의 재설정 링크로 사용자가 정한다. */}
      <Modal
        open={createOpen}
        onClose={closeCreate}
        title="계정 만들기"
        icon={<UserPlus size={16} aria-hidden="true" />}
        tone="accent"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={closeCreate}>취소</Button>
            <Button variant="primary" size="sm" disabled={!canCreate || createMut.isPending}
              onClick={() => createMut.mutate()}>
              {createMut.isPending ? "만드는 중…" : "만들기"}
            </Button>
          </>
        }
      >
        <form
          className="space-y-4"
          onSubmit={(e) => { e.preventDefault(); if (canCreate && !createMut.isPending) createMut.mutate(); }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="new-user-email">이메일</Label>
            <Input id="new-user-email" type="email" autoComplete="off" placeholder="name@example.com"
              value={draft.email} onChange={(e) => setDraft((d) => ({ ...d, email: e.target.value }))} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-user-name">이름</Label>
            <Input id="new-user-name" type="text" maxLength={100}
              value={draft.display_name} onChange={(e) => setDraft((d) => ({ ...d, display_name: e.target.value }))} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-user-role">플랫폼 역할</Label>
            <Select id="new-user-role" value={draft.global_role}
              onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setDraft((d) => ({ ...d, global_role: e.target.value as GlobalRole }))}>
              {GLOBAL_ROLES.map((r) => <option key={r} value={r}>{globalRoleLabel(r)}</option>)}
            </Select>
            <p className="text-caption text-label-3">{ROLE_SCOPE[draft.global_role]}</p>
          </div>
          <p className="text-footnote text-label-2">프로젝트 참여는 각 프로젝트의 멤버 화면에서 정합니다.</p>
        </form>
      </Modal>

      {/* 플랫폼 역할 변경 확인 창. 지금과 바꾼 뒤를 나란히 두어 달라지는 것 둘만 비교한다. */}
      {roleChange && (() => {
        const rc = roleChange;
        const risky = rc.to === "super_admin" || rc.from === "super_admin";
        const close = () => setRoleChange(null);
        return (
          <Modal
            open
            onClose={close}
            title="플랫폼 역할 변경"
            icon={risky ? <ShieldAlert size={16} aria-hidden="true" /> : undefined}
            tone={risky ? "warn" : undefined}
            footer={
              <>
                <Button variant="secondary" size="sm" onClick={close}>취소</Button>
                {/* 버튼 글자는 결과로 쓴다. 조사는 받침을 보고 고른다(`shared/lib/josa`). */}
                <Button variant="primary" size="sm" disabled={roleMut.isPending}
                  onClick={() => roleMut.mutate({ uid: rc.uid, role: rc.to }, { onSettled: close })}>
                  {roleMut.isPending ? "변경 중…" : `${withJosa(globalRoleLabel(rc.to), "으로", "로")} 변경`}
                </Button>
              </>
            }
          >
            <ModalTarget>{rc.name} · {rc.email}</ModalTarget>
            <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-stretch gap-3">
              <div className="rounded-card border border-separator bg-canvas-2 px-3.5 py-3">
                <p className="text-caption text-label-3">지금</p>
                <p className="mt-1 text-body font-semibold text-label">{globalRoleLabel(rc.from)}</p>
                <p className="mt-1 text-footnote text-label-2">{ROLE_SCOPE[rc.from]}</p>
              </div>
              <ArrowRight size={16} className="self-center text-label-3" aria-hidden="true" />
              <div className="rounded-card border border-label px-3.5 py-3">
                <p className="text-caption text-label-3">바꾼 뒤</p>
                <p className="mt-1 text-body font-semibold text-label">{globalRoleLabel(rc.to)}</p>
                <p className="mt-1 text-footnote text-label-2">{ROLE_SCOPE[rc.to]}</p>
              </div>
            </div>
            <p className="mt-3 text-caption text-label-3">바꾼 기록이 남습니다.</p>
          </Modal>
        );
      })()}

      {/* 정지 해제 확인 창. 해제하면 바로 로그인되므로 대상을 확인한다. */}
      <Modal
        open={unsuspendTarget !== null}
        onClose={() => setUnsuspendTarget(null)}
        title="정지 해제"
        tone="neutral"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setUnsuspendTarget(null)}>취소</Button>
            <Button variant="primary" size="sm" disabled={statusMut.isPending}
              onClick={() => unsuspendTarget && statusMut.mutate(
                { uid: unsuspendTarget.uid, status: "active" },
                { onSettled: () => setUnsuspendTarget(null) },
              )}>
              {statusMut.isPending ? "해제 중…" : "정지 해제"}
            </Button>
          </>
        }
      >
        {unsuspendTarget && <ModalTarget>{unsuspendTarget.name} · {unsuspendTarget.email}</ModalTarget>}
        <p className="text-body font-semibold text-label">다시 로그인할 수 있습니다</p>
        <p className="mt-1 text-footnote text-label-2">플랫폼 역할과 프로젝트 참여는 정지 전 그대로 남습니다.</p>
      </Modal>

      {/* 정지 확인 창. 되돌릴 수 있으므로 주의 톤과 먹색 버튼을 쓴다. */}
      <Modal
        open={suspendTarget !== null}
        onClose={() => setSuspendTarget(null)}
        title="사용자 정지"
        icon={<Ban size={16} aria-hidden="true" />}
        tone="warn"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setSuspendTarget(null)}>취소</Button>
            <Button variant="primary" size="sm" disabled={statusMut.isPending}
              // 요청이 끝날 때까지 창을 닫지 않는다. 진행 중인지 보이고 두 번 누를 수 없다.
              onClick={() => suspendTarget && statusMut.mutate(
                { uid: suspendTarget.uid, status: "suspended" },
                { onSettled: () => setSuspendTarget(null) },
              )}>
              {statusMut.isPending ? "정지 중…" : "정지"}
            </Button>
          </>
        }
      >
        {suspendTarget && <ModalTarget>{suspendTarget.name} · {suspendTarget.email}</ModalTarget>}
        <p className="text-body font-semibold text-label">로그인이 막힙니다</p>
        <p className="mt-1 text-footnote text-label-2">정지 해제로 로그인을 되돌립니다.</p>
        {/*
          서버(`work.service.release_holds`)가 모든 프로젝트의 라벨링·검수 보유를 대기열로 돌린다.
          보유 작업 수는 쓰지 않는다. `UserDTO`에 보유량이 없어 없는 수를 지어내지 않는다.
        */}
        <div className="mt-4 rounded-card bg-canvas-2 px-3.5 py-3">
          <p className="text-footnote font-semibold text-label">보유 중인 작업은 대기열로 돌아갑니다</p>
          <p className="mt-1 text-caption text-label-2">
            모든 프로젝트에서 보유한 라벨링·검수 작업이 대기열로 돌아가고 작성하던 내용은 지워집니다.
          </p>
          <p className="mt-1 text-caption text-label-2">정지를 풀어도 작업은 다시 배정되지 않습니다.</p>
        </div>
      </Modal>

      {/* 재설정 링크 발급 확인 창. 누르기 전에 무엇이 만들어지는지 알린다. */}
      <Modal
        open={resetTarget !== null}
        onClose={() => setResetTarget(null)}
        title="비밀번호 재설정 링크 발급"
        icon={<KeyRound size={16} aria-hidden="true" />}
        tone="neutral"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setResetTarget(null)}>취소</Button>
            <Button variant="primary" size="sm" disabled={resetMut.isPending}
              onClick={() => resetTarget && resetMut.mutate(resetTarget)}>
              {resetMut.isPending ? "발급 중…" : "발급"}
            </Button>
          </>
        }
      >
        {resetTarget && <ModalTarget>{resetTarget.name} · {resetTarget.email}</ModalTarget>}
        <p className="text-body font-semibold text-label">링크는 다음 창에서 한 번만 보입니다</p>
        <ul className="mt-3 list-disc space-y-1 pl-5 text-footnote text-label-2">
          <li>한 번 쓰면 무효</li>
          <li>새로 발급하면 앞서 발급한 링크도 무효</li>
          <li>발급 기록은 남고, 링크 값은 남지 않음</li>
        </ul>
      </Modal>

      {/* 발급 결과 창. 링크를 보여 주는 유일한 곳이다. */}
      <ResetLinkModal issued={issued} onClose={() => setIssued(null)} />

      {/* 활동 이력 창. 시각이 맨 앞인 것은 두 로그 장부와 같은 순서다. */}
      <Modal
        open={!!activityUser}
        onClose={() => setActivityUser(null)}
        title={activityUser ? `${activityUser.name} 활동 이력` : "활동 이력"}
        size="lg"
        footer={
          <>
            <Button variant="secondary" size="sm" className="ml-auto" onClick={() => setActivityUser(null)}>닫기</Button>
          </>
        }
      >
        {/* 0건이면 머리 줄을 두지 않는다. "최근 0건"과 "활동 없음"이 같은 사실을 두 번 표시한다. */}
        {!!activity.data?.length && (
          <ModalTarget>
            {/* 상한에 닿았으면 그 사실을 알린다. 서버가 총수를 주지 않아 받은 수를 기준으로 쓴다. */}
            {activity.data.length >= ACTIVITY_LIMIT
              ? `최근 ${ACTIVITY_LIMIT}건까지만 · 최신순`
              : `최근 ${activity.data.length.toLocaleString("ko-KR")}건 · 최신순`}
          </ModalTarget>
        )}
        {activity.isLoading ? (
          <div role="status" aria-label="활동 이력 불러오는 중"><SkeletonText lines={5} /></div>
        ) : activity.isError ? (
          <ErrorState inline message="활동 이력 조회 실패" onRetry={() => void activity.refetch()} />
        ) : !activity.data?.length ? (
          <EmptyState dense message="활동 없음" />
        ) : (
          <div className="overflow-hidden rounded-card border border-separator">
            <table className="w-full text-footnote">
              <thead className="bg-canvas-2 text-caption font-semibold text-label-2">
                <tr>
                  <th scope="col" className="px-3 py-2 text-left">시각</th>
                  <th scope="col" className="px-3 py-2 text-left">활동</th>
                  <th scope="col" className="px-3 py-2 text-left">프로젝트</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-separator">
                {activity.data.map((e) => {
                  const pr = projectOf(e.project_id);
                  return (
                    <tr key={e.event_id}>
                      {/* 시각은 감사 기록 시각 형식(`fmtEventTime`)으로 적는다. */}
                      <td className="whitespace-nowrap px-3 py-2 font-mono text-caption tabular-nums text-label-2">{fmtEventTime(e.occurred_at)}</td>
                      <td className="px-3 py-2 text-label">{humanizeEvent(e.event_type)}</td>
                      <td className={cn("max-w-48 truncate px-3 py-2", pr.quiet ? "text-label-3" : "text-label")}>{pr.text}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Modal>
    </div>
  );
}

/**
 * 발급된 재설정 링크를 한 번 보여 주는 창을 그린다.
 *
 * 계정을 막 만든 경우(`created`)에는 같은 링크가 첫 비밀번호를 정하는 링크라서 제목과 안내만 바꾼다.
 * 링크 주소는 화면이 만든다. 서버에는 이 배포의 웹 주소 정보가 없고, 관리자가 지금 보고 있는 주소가
 * 사용자가 들어올 주소다. 설정을 새로 두고 잘못 채울 위험을 만드는 대신 `origin`을 쓴다.
 * 링크는 읽기 전용 입력란에 담는다. 브라우저가 복사를 막아도 직접 선택해 복사할 수 있다.
 */
function ResetLinkModal({ issued, onClose }: { issued: (ResetIssued & { name: string; created?: boolean }) | null; onClose: () => void }) {
  const { toast } = useToast();
  // 토큰은 주소 조각에 싣는다. 조각은 브라우저가 서버로 보내지 않아 웹 서버 접근 로그에 남지 않는다.
  const link = issued ? `${window.location.origin}/password-reset#token=${encodeURIComponent(issued.token)}` : "";

  const copy = () =>
    copyText(link).then(
      () => toast("success", "재설정 링크를 복사했습니다"),
      // 입력란은 포커스하면 전체가 선택된다. 직접 복사하는 방법을 안내한다.
      () => toast("error", "복사 실패", "링크를 직접 선택해 복사합니다."),
    );

  return (
    <Modal
      open={issued !== null}
      onClose={onClose}
      // 제목은 명사구다. 발급이 끝났다는 것은 확인 표시로 알린다.
      title={issued?.created ? "비밀번호 설정 링크" : "재설정 링크"}
      icon={<Check size={16} aria-hidden="true" />}
      tone="accent"
      footer={<Button variant="secondary" size="sm" onClick={onClose}>닫기</Button>}
    >
      {issued && <ModalTarget>{issued.name} · {issued.email}</ModalTarget>}
      {/* 가장 중요한 한 가지를 링크보다 먼저 둔다. 굵은 결과와 작은 안내로 나눈다. */}
      <div className="flex gap-2.5 rounded-card bg-warn-muted px-3.5 py-3">
        <TriangleAlert size={16} className="mt-0.5 shrink-0 text-warn-text" aria-hidden="true" />
        <div>
          <p className="text-footnote font-semibold text-warn-text">이 창을 닫으면 링크를 다시 볼 수 없습니다</p>
          <p className="mt-0.5 text-caption text-label-2">
            {issued?.created ? "계정을 만들었습니다. 지금 복사해 본인에게 전달하면 본인이 비밀번호를 정합니다." : "지금 복사해 본인에게 전달합니다."}
          </p>
        </div>
      </div>
      <div className={cn(FIELD_BASE, "mt-3 flex items-center gap-2 py-1 pl-3 pr-1 focus-within:border-focus focus-within:ring-2 focus-within:ring-focus/25")}>
        <input
          readOnly
          value={link}
          aria-label="재설정 링크"
          // 포커스하면 전체를 선택하고, 토큰 끝 대신 주소 앞부분이 보이게 스크롤을 되돌린다.
          onFocus={(e) => { e.currentTarget.select(); e.currentTarget.scrollLeft = 0; }}
          className="min-w-0 flex-1 bg-transparent font-mono text-caption text-label focus:outline-none"
        />
        <Button type="button" variant="primary" size="sm" onClick={copy}>
          <Copy size={14} aria-hidden="true" />
          복사
        </Button>
      </div>
      {/* 만료는 날짜와 시각으로 쓴다. 시각만 쓰면 자정을 넘길 때 어느 날인지 알 수 없다. 유효 시간은 서버 값이다. */}
      <dl className="mt-3 grid grid-cols-[48px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-footnote">
        <dt className="text-label-3">만료</dt>
        <dd className="tabular-nums text-label">
          {issued ? fmtEventTime(issued.expires_at).slice(0, 16) : ""} · 발급 후 {issued?.valid_minutes}분
        </dd>
        <dt className="text-label-3">사용</dt>
        <dd className="text-label">한 번 쓰면 무효</dd>
      </dl>
    </Modal>
  );
}

/**
 * 사용자 행 끝 `⋯` 메뉴를 그린다. 드물게 쓰는 조작을 늘 보이게 두지 않되 찾을 수 있게 둔다.
 *
 * 위험 항목은 구분선 아래에 따로 둔다. 표가 가로 스크롤이라 메뉴는 portal(fixed)로 띄우고,
 * 스크롤·리사이즈·바깥 클릭·Esc에 닫는다.
 */
function UserRowMenu({
  label,
  items,
}: {
  label: string;
  items: { key: string; label: string; icon: ReactNode; danger?: boolean; disabled?: boolean; onSelect: () => void }[];
}) {
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  useOutsideClose(open, btnRef, () => setOpen(false), { extraRef: menuRef, reposition: true });

  const toggle = () => {
    if (open) { setOpen(false); return; }
    const r = btnRef.current?.getBoundingClientRect();
    if (r) setPos({ top: r.bottom + 6, right: window.innerWidth - r.right });
    setOpen(true);
  };

  const safe = items.filter((i) => !i.danger);
  const danger = items.filter((i) => i.danger);
  const renderItem = (it: (typeof items)[number]) => (
    <button
      key={it.key}
      type="button"
      role="menuitem"
      disabled={it.disabled}
      onClick={() => { setOpen(false); it.onSelect(); }}
      className={cn(
        "flex w-full items-center gap-2.5 rounded-control px-2.5 py-2 text-left text-footnote transition-colors disabled:text-label-4",
        it.danger ? "text-bad-text hover:bg-bad-muted" : "text-label hover:bg-canvas-2",
      )}
    >
      {it.icon}
      {it.label}
    </button>
  );

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={toggle}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        className={cn(
          "inline-flex size-8 items-center justify-center rounded-control text-label-2 transition-colors hover:bg-canvas-2 hover:text-label focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
          open && "border border-separator-strong bg-panel",
        )}
      >
        <Ellipsis size={16} aria-hidden="true" />
      </button>
      {open && pos && createPortal(
        <div
          ref={menuRef}
          role="menu"
          style={{ top: pos.top, right: pos.right }}
          className="fixed z-50 w-60 origin-top-right animate-pop-in overflow-hidden rounded-card border border-separator bg-raised p-1 shadow-pop"
        >
          {safe.map(renderItem)}
          {danger.length > 0 && safe.length > 0 && <div role="separator" className="my-1 border-t border-separator" />}
          {danger.map(renderItem)}
        </div>,
        document.body,
      )}
    </>
  );
}

/** 사용자를 바꾼 뒤(계정 만들기, 역할 변경, 정지·해제) 전체 사용자 조회를 무효화한다. */
function invalidateAll(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: platformKeys.allUsers() });
}
