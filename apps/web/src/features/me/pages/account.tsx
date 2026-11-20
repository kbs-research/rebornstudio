/**
 * `내 계정` 화면(`/account`). 본인 프로필, 비밀번호, 로그인 기록을 다룬다.
 *
 * 설정 화면 유형이다. 왼쪽 절 목록(`SectionRail`)과 오른쪽 카드(기본 정보·비밀번호·로그인 기록)를 둔다.
 * 프로필은 바뀐 항목이 있을 때만 뜨는 흑연색 저장 막대로 저장한다. 바뀐 항목 이름을 막대에 쓴다.
 * 비밀번호는 자기 카드 바닥에서 따로 바꾼다. 두 저장이 한 버튼으로 섞이지 않는다.
 *
 * 서버는 PATCH /users/me(UserProfileUpdate)다.
 * 이 화면에 오는 사람은 이미 필수 동의를 했다. 필수 동의 기록이 없으면 보호 셸이 동의 화면을 먼저 그린다.
 * 필수 동의는 계정을 쓰는 조건이라 이 화면에서 따로 철회하지 않는다. 동의 시각만 보여 주고 철회는 계정 삭제 요청으로 안내한다.
 * 선택 개인정보(전화, 생년월일, 주소)는 받지 않는다. 이전에 선택 동의하고 값을 남긴 사람에게만 개인정보 카드를 그려
 * 동의를 철회하게 한다. 철회하면 서버가 선택 정보를 바로 지우므로 확인 창에 지워질 값을 보여 준다.
 *
 * 비밀번호를 바꾸면 서버가 이전 토큰을 모두 막으므로 성공하면 로그인 화면으로 보낸다.
 */
import { useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { ExternalLink, Lock } from "lucide-react";

import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { Checkbox } from "@/shared/ui/Checkbox";
import { Chip } from "@/shared/ui/Chip";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { Input } from "@/shared/ui/Input";
import { Modal } from "@/shared/ui/Modal";
import { PageHeader } from "@/shared/ui/PageHeader";
import { PageSkeleton } from "@/shared/ui/PageSkeleton";
import { SectionRail } from "@/shared/ui/SectionRail";
import { Skeleton } from "@/shared/ui/Skeleton";
import { useToast } from "@/shared/ui/Toast";
import { type ApiError, type UserDTO } from "@/shared/api/client";
import { cn } from "@/shared/lib/cn";
import { apiErrorText, type ErrorText } from "@/shared/api/error_text";
import { meetsPasswordPolicy, PasswordRules } from "@/features/auth";
import { fmtDate, fmtDateTime, fmtFullDateTime } from "@/shared/lib/time";
import { useAuth } from "@/store/auth";
import { lockDetail } from "../lib/login_lock";

import {
  changeMyPassword,
  fetchMyAuthEvents,
  meKeys,
  updateMyProfile,
  withdrawOptional,
} from "../api";

/**
 * User-Agent를 사람이 읽는 짧은 이름(OS와 브라우저)으로 바꾼다. 원문은 툴팁에 그대로 둔다.
 *
 * 로그인 이력에서 사용자가 판단할 것은 "본인의 접속인가" 하나다.
 * 200자짜리 원문을 그대로 보여 주면 그 판단이 오히려 어렵다.
 * 그래서 OS와 브라우저만 뽑고, 더 확인하려면 툴팁을 본다.
 * 알아볼 수 없는 문자열이면 지어내지 않고 `기타`를 돌려준다.
 */
function deviceLabel(ua: string): string {
  const os =
    /iPhone|iPad/.test(ua) ? "iOS"
    : /Android/.test(ua) ? "Android"
    : /Mac OS X/.test(ua) ? "Mac"
    : /Windows/.test(ua) ? "Windows"
    : /Linux/.test(ua) ? "Linux"
    : null;
  const browser =
    /Edg\//.test(ua) ? "Edge"
    : /OPR\//.test(ua) ? "Opera"
    : /Chrome\//.test(ua) ? "Chrome"
    : /Firefox\//.test(ua) ? "Firefox"
    : /Safari\//.test(ua) ? "Safari"
    : null;
  return [os, browser].filter(Boolean).join(" · ") || "기타";
}

/**
 * 로그인 이력 한 줄의 이름과 색. 서버 로그인 이력(`login_history._AUTH_EVENT_TYPES`)의 네 종류와 1:1로 대응한다.
 * 새 종류가 생기면 이 표에 더한다. 두 경우만 나누는 분기로 바꾸면 로그인 실패가 "로그아웃"으로 표시된다.
 * `warn`이 참인 종류는 줄 배경을 주의 색으로 칠한다.
 */
const AUTH_KIND: Record<string, { label: string; dot: string; warn?: boolean }> = {
  "auth.login": { label: "로그인", dot: "bg-ok" },
  "auth.logout": { label: "로그아웃", dot: "bg-label-4" },
  "auth.login_failed": { label: "로그인 실패", dot: "bg-warn", warn: true },
  "auth.login_locked": { label: "로그인 잠금", dot: "bg-warn", warn: true },
};

/**
 * 로그인 실패 사유(서버 `auth.service._record_login_failure`의 `reason`)를 화면 문구로 바꾼다.
 * 사전에 없는 값은 사유 없이 `로그인 실패`만 쓴다. 코드를 그대로 보여 주지 않는다.
 */
const FAIL_REASON: Record<string, string> = {
  bad_password: "비밀번호 불일치",
  blocked: "로그인이 제한된 계정",
};

/** 로그인 기록을 처음에 보여 주는 수. 나머지는 `더 보기`로 연다. 안쪽 스크롤을 두지 않는다. */
const AUTH_SHOWN = 10;

/** 로그인 기록을 받는 수. 서버 조회의 `limit`와 화면의 분모 문구가 같이 쓴다. */
const AUTH_LIMIT = 50;

/** 프로필 폼 값. 서버 값과 비교해 바뀐 항목을 센다. */
type ProfileForm = { display_name: string };

/** 프로필 항목의 화면 이름. 저장 막대가 바뀐 항목을 이 이름으로 부른다. */
const FIELD_NAME: Record<keyof ProfileForm, string> = {
  display_name: "표시 이름",
};

/** 철회 확인 창이 지워질 값을 보여 주는 선택 정보 항목. */
const OPTIONAL_FIELD_NAME = { phone_number: "전화번호", birth_date: "생년월일", address: "주소" } as const;

/** 사용자 값으로 폼을 채운다. 없는 값은 빈 글자다. */
function formOf(u: UserDTO | null): ProfileForm {
  return { display_name: u?.display_name ?? "" };
}

/** 설정 카드 한 장을 그린다. 절 목록이 가리키는 `id`가 있다. */
function SettingsCard({ id, title, extra, children, footer }: {
  id: string;
  title: string;
  extra?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <Card as="section" id={id} aria-labelledby={`${id}-title`} className="scroll-mt-6">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 border-b border-separator px-6 py-4">
        <h2 id={`${id}-title`} className="text-headline text-label">{title}</h2>
        {extra}
      </div>
      {children}
      {footer}
    </Card>
  );
}

/** 설정 한 줄을 그린다. 이름 176px, 입력 420px이다. 입력 아래 안내는 `children`으로 둔다. */
function Row({ label, htmlFor, children }: { label: string; htmlFor?: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[11rem_minmax(0,26.25rem)] items-start gap-x-6 border-b border-separator px-6 py-4 last:border-b-0">
      {htmlFor ? (
        <label htmlFor={htmlFor} className="pt-2 text-footnote font-semibold text-label">{label}</label>
      ) : (
        <span className="pt-2 text-footnote font-semibold text-label">{label}</span>
      )}
      <div className="min-w-0 space-y-1.5">{children}</div>
    </div>
  );
}

/** 내 계정 화면을 그린다. 프로필, 개인정보 동의, 비밀번호 변경, 로그인 기록을 보여 준다. */
export function AccountPage() {
  const { user, loadMe, signOut } = useAuth();
  const { toast } = useToast();
  const navigate = useNavigate();

  const [form, setForm] = useState<ProfileForm>(() => formOf(user));
  const [saving, setSaving] = useState(false);
  // 선택 동의를 철회하는 중이면 true.
  const [optBusy, setOptBusy] = useState(false);
  const [confirmWithdraw, setConfirmWithdraw] = useState(false);

  // 비밀번호 변경
  // 확인 입력을 둔다. 바꾸면 모든 기기에서 로그아웃되고 되찾으려면 관리자 링크가 필요해,
  // 오타를 확인할 단계가 필요하다.
  const [pwd, setPwd] = useState({ old: "", next: "", confirm: "" });
  const [showPwd, setShowPwd] = useState(false);
  const [changing, setChanging] = useState(false);
  const [pwdError, setPwdError] = useState<ErrorText | null>(null);
  const [allAuth, setAllAuth] = useState(false);

  async function changePassword() {
    setChanging(true);
    setPwdError(null);
    try {
      // 이 요청의 401은 "현재 비밀번호가 틀렸다"는 뜻이다. 호출이 `expect401`을 켜서 세션 만료로 읽지 않는다.
      await changeMyPassword(pwd.old, pwd.next);
      setPwd({ old: "", next: "", confirm: "" });
      // 서버가 이전 토큰을 모두 막았으므로 이 세션도 이미 끝났다. 세션을 정리한 뒤 로그인 화면으로 보낸다.
      toast("success", "비밀번호를 변경했습니다", "새 비밀번호로 다시 로그인하세요.");
      signOut();
      navigate("/login", { replace: true });
    } catch (e) {
      // 401은 현재 비밀번호가 틀렸다는 뜻이다. 나머지는 오류 사전이 구분한다.
      setPwdError(
        (e as ApiError).status === 401 ? { message: "현재 비밀번호가 맞지 않습니다" } : apiErrorText(e, "비밀번호 변경 실패"),
      );
    } finally {
      setChanging(false);
    }
  }

  // 본인의 로그인, 로그인 실패, 로그인 잠금, 로그아웃 이력(domain_events의 auth.*)
  const authEvents = useQuery({
    queryKey: meKeys.authEvents(),
    queryFn: () => fetchMyAuthEvents(AUTH_LIMIT),
    enabled: !!user,
    meta: { handled: true },
  });

  // 마운트할 때 user가 아직 없었으면(loadMe 진행 중) user가 도착한 뒤 폼을 맞춘다.
  useEffect(() => {
    if (!user) return;
    setForm(formOf(user));
  }, [user?.user_id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!user) return <PageSkeleton header panels={2} className="w-full max-w-4xl" />;

  const saved = formOf(user);
  const changed = (Object.keys(FIELD_NAME) as (keyof ProfileForm)[]).filter((k) => form[k].trim() !== saved[k].trim());
  const dirty = changed.length > 0;

  /** 프로필을 저장한다. 서버가 다듬은 값으로 폼을 다시 맞춘다. */
  const save = async () => {
    setSaving(true);
    try {
      const out = await updateMyProfile({ display_name: form.display_name.trim() || undefined });
      setForm(formOf(out));
      await loadMe();
      toast("success", "프로필을 저장했습니다");
    } catch (e) {
      // 오류 사전이 사유(검증 실패, 일시 오류)를 구분한다.
      const t = apiErrorText(e, "저장 실패");
      toast("error", t.message, t.hint);
    } finally {
      setSaving(false);
    }
  };

  /** 선택 동의를 철회한다. 서버가 선택 정보를 지우고, 계정을 다시 받아 개인정보 카드를 닫는다. */
  const withdrawOptionalConsent = async () => {
    setOptBusy(true);
    try {
      await withdrawOptional();
      await loadMe();
      setConfirmWithdraw(false);
      toast("success", "선택 동의를 철회했습니다", "선택 정보를 지웠습니다.");
    } catch (e) {
      const t = apiErrorText(e, "선택 동의 철회 실패");
      toast("error", t.message, t.hint);
    } finally {
      setOptBusy(false);
    }
  };
  const optionalOn = !!user.optional_privacy_consented_at;

  const pwdMismatch = !!pwd.confirm && pwd.confirm !== pwd.next;
  const authRows = authEvents.data ?? [];
  const authShown = allAuth ? authRows : authRows.slice(0, AUTH_SHOWN);
  /* 분모를 밝힌다. 서버는 최근 `AUTH_LIMIT`건까지만 준다. 그 수를 채웠으면 더 오래된 기록이 있을 수 있다. */
  const authScope = authRows.length >= AUTH_LIMIT ? `최근 ${AUTH_LIMIT}건` : `전체 ${authRows.length}건`;
  const authCount = !authEvents.data
    ? undefined
    : authShown.length < authRows.length
      ? `${authScope} 중 ${authShown.length}건`
      : authScope;

  return (
    <div className="w-full max-w-5xl">
      <PageHeader title="내 계정" />
      <div className="grid grid-cols-[9.5rem_minmax(0,47.5rem)] items-start gap-8">
        <SectionRail
          className="sticky top-6"
          sections={[
            { id: "basic", label: "기본 정보" },
            ...(optionalOn ? [{ id: "personal", label: "개인정보" }] : []),
            { id: "password", label: "비밀번호" },
            { id: "logins", label: "로그인 기록" },
          ]}
        />
        <div className="min-w-0 space-y-5">
          <SettingsCard id="basic" title="기본 정보">
            <Row label="표시 이름" htmlFor="display_name">
              <Input
                id="display_name"
                autoComplete="name"
                value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              />
            </Row>
            <Row label="이메일" htmlFor="email">
              <div className="relative">
                <Input id="email" value={user.email} disabled aria-describedby="email-help" className="pr-9" />
                <Lock size={14} aria-hidden="true" className="absolute right-3 top-1/2 -translate-y-1/2 text-label-3" />
              </div>
              <p id="email-help" className="text-caption text-label-2">바꿀 수 없습니다.</p>
            </Row>
            {/*
              필수 동의는 계정을 쓰는 조건이라 읽기 전용이다. 이 화면에 온 사람은 이미 동의했다.
              날짜 표기는 `shared/lib/time.fmtDate`(`9월 1일`)를 쓴다.
            */}
            <Row label="필수 동의">
              <p className="pt-2 text-footnote text-label">
                {user.privacy_consented_at ? (
                  <>
                    <time dateTime={user.privacy_consented_at} title={fmtFullDateTime(user.privacy_consented_at)}>
                      {fmtDate(user.privacy_consented_at)}
                    </time>
                    에 동의함
                  </>
                ) : (
                  "동의 기록 없음"
                )}
              </p>
              <p className="flex flex-wrap items-center gap-x-3 text-caption text-label-2">
                <span>필수 동의 철회는 계정 삭제로 처리합니다. 최고관리자에게 요청합니다.</span>
                <Link to="/privacy" target="_blank" className="inline-flex items-center gap-0.5 font-semibold text-label underline underline-offset-2">
                  동의 내용 보기 <ExternalLink size={12} aria-hidden="true" />
                </Link>
              </p>
            </Row>
          </SettingsCard>

          {/* 이전에 선택 동의하고 값을 남긴 사람에게만 그린다. 새 선택 동의와 입력은 받지 않는다. */}
          {optionalOn && (
            <SettingsCard id="personal" title="개인정보" extra={<Chip>선택</Chip>}>
              <Row label="선택 동의">
                <p className="pt-2 text-footnote text-label">
                  <time dateTime={user.optional_privacy_consented_at!} title={fmtFullDateTime(user.optional_privacy_consented_at!)}>
                    {fmtDate(user.optional_privacy_consented_at!)}
                  </time>
                  에 동의함
                </p>
                <p className="text-caption text-label-2">철회하면 저장된 선택 정보를 바로 지웁니다.</p>
                <div className="pt-1">
                  <Button size="sm" variant="secondary" disabled={optBusy} onClick={() => setConfirmWithdraw(true)}>
                    동의 철회
                  </Button>
                </div>
              </Row>
            </SettingsCard>
          )}

          {/*
            비밀번호 카드. 바꾸면 서버가 `password_changed_at`을 기록해 먼저 발급된 토큰을 모두 막는다.
            그래서 성공하면 이 세션도 끝난다. 이 사실을 누르기 전에 안내한다.
          */}
          <SettingsCard
            id="password"
            title="비밀번호"
            footer={
              <div className="flex items-center justify-end gap-5 rounded-b-card border-t border-separator bg-canvas-2 px-6 py-3.5">
                <Checkbox checked={showPwd} onChange={setShowPwd} label="비밀번호 표시" />
                <Button
                  variant="secondary"
                  onClick={changePassword}
                  disabled={changing || !pwd.old || !meetsPasswordPolicy(pwd.next) || pwd.confirm !== pwd.next}
                >
                  {changing ? "변경 중…" : "비밀번호 변경"}
                </Button>
              </div>
            }
          >
            {/* 결과(굵게)와 할 일(작게)을 나눈다. */}
            <div className="border-b border-separator px-6 py-4">
              <p className="text-footnote font-semibold text-label">변경하면 모든 기기에서 로그아웃됩니다.</p>
              <p className="text-caption text-label-2">새 비밀번호로 다시 로그인합니다.</p>
            </div>
            <Row label="현재 비밀번호" htmlFor="old_password">
              <Input
                id="old_password"
                type={showPwd ? "text" : "password"}
                autoComplete="current-password"
                value={pwd.old}
                onChange={(e) => setPwd({ ...pwd, old: e.target.value })}
              />
            </Row>
            <Row label="새 비밀번호" htmlFor="new_password">
              <Input
                id="new_password"
                type={showPwd ? "text" : "password"}
                autoComplete="new-password"
                value={pwd.next}
                aria-describedby="new_password_rules"
                onChange={(e) => setPwd({ ...pwd, next: e.target.value })}
              />
              {/*
                규칙 목록은 비밀번호 재설정·최초 설정 화면과 같은 공용 부품이다(`features/auth/lib/password_policy`).
                입력 전부터 보인다. 규칙 목록으로 버튼이 꺼진 이유를 알 수 있다.
                미충족은 흐린 점이다. 입력 전의 × 표시는 아직 틀린 것이 없는데 실패처럼 읽힌다.
              */}
              <div id="new_password_rules" role="group" aria-label="비밀번호 규칙">
                <PasswordRules value={pwd.next} always />
              </div>
            </Row>
            <Row label="새 비밀번호 확인" htmlFor="confirm_password">
              <Input
                id="confirm_password"
                type={showPwd ? "text" : "password"}
                autoComplete="new-password"
                placeholder="비밀번호 다시 입력"
                value={pwd.confirm}
                onChange={(e) => setPwd({ ...pwd, confirm: e.target.value })}
                aria-invalid={pwdMismatch || undefined}
                aria-describedby={pwdMismatch ? "confirm_password_err" : undefined}
              />
              {pwdMismatch && (
                <p id="confirm_password_err" className="text-caption text-bad-text">
                  비밀번호가 일치하지 않습니다.
                </p>
              )}
              {pwdError && (
                <div role="alert">
                  <p className="text-caption font-semibold text-bad-text">{pwdError.message}</p>
                  {pwdError.hint && <p className="text-caption text-label-2">{pwdError.hint}</p>}
                </div>
              )}
            </Row>
          </SettingsCard>

          {/* 로그인 기록. 본인의 최근 로그인, 실패, 로그아웃을 읽기 전용으로 보여 준다. */}
          <SettingsCard
            id="logins"
            title="로그인 기록"
            extra={authCount ? <Chip>{authCount}</Chip> : undefined}
            footer={
              authRows.length > 0 ? (
                <div className="flex items-center justify-between gap-3 border-t border-separator px-6 py-3">
                  <p className="text-footnote text-label-2">모르는 접속이 있으면 비밀번호를 변경합니다.</p>
                  {!allAuth && authRows.length > AUTH_SHOWN && (
                    <Button size="sm" variant="secondary" onClick={() => setAllAuth(true)}>
                      더 보기
                    </Button>
                  )}
                </div>
              ) : undefined
            }
          >
            {authEvents.isLoading ? (
              <div role="status" aria-label="로그인 기록 불러오는 중" className="space-y-2.5 px-6 py-4">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-8 w-full" />
                ))}
              </div>
            ) : authEvents.isError ? (
              // 조회 실패를 "기록 없음"으로 그리지 않는다. 본인이 하지 않은 접속을 찾는 화면이다.
              <div className="px-6 py-4">
                <ErrorState inline message="로그인 기록 조회 실패" onRetry={() => void authEvents.refetch()} />
              </div>
            ) : authRows.length === 0 ? (
              <EmptyState dense message="로그인 기록 없음" />
            ) : (
              <table className="w-full text-footnote">
                <thead className="bg-canvas-2 text-left text-caption font-semibold text-label-2">
                  <tr>
                    <th scope="col" className="px-6 py-2.5 font-semibold">종류</th>
                    <th scope="col" className="px-3 py-2.5 font-semibold">시각</th>
                    <th scope="col" className="px-3 py-2.5 font-semibold">기기</th>
                    <th scope="col" className="px-6 py-2.5 font-semibold">IP</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-separator">
                  {authShown.map((e) => {
                    /* 두 경우만 나누면 세 번째 종류가 다른 쪽으로 표시된다. 사전에 없으면 `인증 기록`이다. */
                    const kind = AUTH_KIND[e.event_type] ?? { label: "인증 기록", dot: "bg-label-4" };
                    /* 접속 출처(IP, User-Agent)는 이전 행에는 없다. 없으면 지어내지 않는다. */
                    const origin = (e.payload ?? {}) as {
                      ip?: string | null; user_agent?: string | null; reason?: string | null;
                      via?: string | null; failures?: number | null; lock_seconds?: number | null;
                    };
                    const reason =
                      e.event_type === "auth.login_failed" ? (origin.reason ? FAIL_REASON[origin.reason] : undefined)
                      : e.event_type === "auth.login_locked" ? lockDetail(origin)
                      : undefined;
                    return (
                      <tr key={e.event_id} className={cn(kind.warn && "bg-warn-muted")}>
                        <td className="px-6 py-2.5">
                          <span className="inline-flex items-center gap-2 text-label">
                            <span className={cn("size-1.5 shrink-0 rounded-capsule", kind.dot)} aria-hidden="true" />
                            {kind.label}
                          </span>
                          {reason && <span className="block pl-3.5 text-caption text-warn-text">{reason}</span>}
                        </td>
                        {/* 날짜를 비교해야 하는 목록이라 절대 시각을 쓴다. */}
                        <td className="px-3 py-2.5 tabular-nums text-label">
                          <time dateTime={e.occurred_at} title={fmtFullDateTime(e.occurred_at)}>
                            {fmtDateTime(e.occurred_at)}
                          </time>
                        </td>
                        <td className="px-3 py-2.5 text-label" title={origin.user_agent ?? undefined}>
                          {origin.user_agent ? deviceLabel(origin.user_agent) : "—"}
                        </td>
                        {/* 빈 값의 —는 본문 글꼴로 쓴다. 고정폭 글꼴에서는 기기 열의 —보다 짧게 보인다. */}
                        <td className={cn("px-6 py-2.5 text-caption text-label-2", origin.ip && "font-mono")}>{origin.ip ?? "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </SettingsCard>
          {/*
            저장 막대. 바뀐 항목이 있을 때만 뜬다. 흑연색 막대라 페이지 위에서 눈에 띈다.
            바뀐 항목 이름을 써서 무엇을 저장하는지 버튼을 누르기 전에 알 수 있다.
          */}
          {dirty && (
            <div
              data-surface="graphite"
              role="region"
              aria-label="저장하지 않은 변경"
              className="sticky bottom-6 z-30 flex items-center gap-3 rounded-overlay bg-panel px-5 py-3 text-label shadow-e3"
            >
              <span aria-hidden="true" className="size-1.5 shrink-0 rounded-capsule bg-amber" />
              <p className="min-w-0 flex-1 truncate text-footnote">
                저장하지 않은 변경 {changed.length}건 · {changed.map((k) => FIELD_NAME[k]).join(", ")}
              </p>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setForm(saved)}
                disabled={saving}
              >
                되돌리기
              </Button>
              <Button size="sm" onClick={save} disabled={saving}>
                {saving ? "저장 중…" : "프로필 저장"}
              </Button>
            </div>
          )}
        </div>
      </div>

      {/* 선택 동의 철회 확인. 지워질 값을 같은 창에 보여 판단 근거로 둔다. */}
      <Modal
        open={confirmWithdraw}
        onClose={() => setConfirmWithdraw(false)}
        title="선택 동의 철회"
        size="sm"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setConfirmWithdraw(false)}>취소</Button>
            <Button variant="danger" size="sm" disabled={optBusy} onClick={() => void withdrawOptionalConsent()}>
              {optBusy ? "철회 중…" : "철회"}
            </Button>
          </>
        }
      >
        <p className="text-body font-semibold text-label">선택 항목이 바로 지워집니다.</p>
        <p className="mt-1 text-footnote text-label-2">필수 동의와 계정은 그대로입니다.</p>
        <dl className="mt-3 divide-y divide-separator rounded-field border border-separator">
          {(Object.keys(OPTIONAL_FIELD_NAME) as (keyof typeof OPTIONAL_FIELD_NAME)[]).map((k) => (
            <div key={k} className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-3 px-3.5 py-2.5 text-footnote">
              <dt className="text-label-2">{OPTIONAL_FIELD_NAME[k]}</dt>
              <dd className="min-w-0 break-words text-label">{user[k] || "—"}</dd>
            </div>
          ))}
        </dl>
      </Modal>
    </div>
  );
}
