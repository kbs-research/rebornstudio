/**
 * 로그인한 사용자의 화면을 감싸는 셸(ProtectedLayout).
 * 왼쪽 전체 높이의 AppSidebar와 콘텐츠(Outlet)로 이뤄진다.
 *
 * 전역 상단 내비게이션 헤더는 두지 않는다.
 * 맥락은 사이드바와 화면별 얇은 헤더(PageHeader)가 보여 주고,
 * 콘텐츠 위의 TopBar는 브레드크럼과 전역 동작만 둔다.
 * 로그인하지 않았으면 /login으로 보낸다. 개인정보 수집·이용 동의 기록이 없으면 셸 대신 동의 화면을 그린다.
 * 사이드바 접기·펼치기는 셸이 맡는다. 단축키 창은 재생 키가 있는 작업 화면이 각자 연다.
 */
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { FolderX, Timer, TriangleAlert } from "lucide-react";
import { Link, Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";

import { ProjectStatusNotice } from "@/shell/ProjectStatusNotice";
import { AppSidebar } from "@/shell/AppSidebar";
import { TopBar } from "@/shell/TopBar";
import { Button, buttonClasses } from "@/shared/ui/Button";
import { Input } from "@/shared/ui/Input";
import { Label } from "@/shared/ui/Label";
import { Modal } from "@/shared/ui/Modal";
import { login, PrivacyConsentPage } from "@/features/auth";
import {
  accessLapsed,
  isAuthed,
  restoreRetryAfter,
  restoreUnavailable,
  sessionEndedBySignOut,
  setSession,
} from "@/shared/api/auth";
import { authGate, loginPath } from "@/app/auth_gate";
import { loginErrorText } from "@/shared/api/error_text";
import { queryClient } from "@/app/queryClient";
import { StatePanel } from "@/shared/ui/StatePanel";
import { PageSkeleton } from "@/shared/ui/PageSkeleton";
import { useMediaQuery } from "@/shared/hooks/useMediaQuery";
import { useSessionKeepalive } from "@/hooks/useSessionKeepalive";
import { layoutBody, nextShownSlug, useProjectParam, useSlugProject } from "@/hooks/useSlugProject";
import { cn } from "@/shared/lib/cn";
import { moveLabel, PROJECT_LIST_LANDING } from "@/lib/roles";
import { useAuth } from "@/store/auth";

const COLLAPSED_KEY = "reborn:sidebar-collapsed";

/**
 * 앱 시작 때 세션 복구가 서버 일시 오류로 끝났을 때 로그인 화면 대신 그리는 패널.
 *
 * 요청 한도(429)면 서버가 준 대기 시간을 세고 끝나면 페이지를 다시 연다. 다시 열면 시작 복구가 처음부터 돈다.
 * 한도에 걸린 것을 연결 실패로 안내하면 사용자는 네트워크를 의심하고 새로고침을 거듭해 한도를 더 쓴다.
 */
function RestoreFailedPanel({ retryAfter }: { retryAfter: number | null }) {
  const [left, setLeft] = useState(retryAfter);
  useEffect(() => {
    if (left === null) return;
    if (left <= 0) {
      window.location.reload();
      return;
    }
    const t = window.setTimeout(() => setLeft(left - 1), 1_000);
    return () => window.clearTimeout(t);
  }, [left]);
  if (left !== null) {
    return (
      <div className="min-h-screen bg-canvas px-4">
        <StatePanel
          icon={<Timer size={18} aria-hidden="true" />}
          title="요청 한도 초과"
          lead="요청이 몰려 로그인 상태 확인을 잠시 미뤘습니다."
          hint={`${Math.max(left, 0)}초 후 자동으로 다시 확인합니다.`}
        />
      </div>
    );
  }
  return (
    <div className="min-h-screen bg-canvas px-4">
      <StatePanel
        tone="bad"
        icon={<TriangleAlert size={18} aria-hidden="true" />}
        title="서버 연결 실패"
        lead="로그인 상태를 확인하지 못했습니다."
        hint="잠시 후 다시 시도해주세요."
        actions={<Button onClick={() => window.location.reload()}>다시 시도</Button>}
      />
    </div>
  );
}

/** 인증을 확인하고 셸(사이드바, 상단바, 본문)을 그린다. */
export function ProtectedLayout() {
  const { slug: urlSlug, state: slugState, retry: retryProjects } = useSlugProject();
  // 이 slug의 본문을 한 번이라도 그렸는지 기억한다. 그린 뒤에는 잠깐 어긋나도 본문을 내리지 않는다(`layoutBody`).
  const [shownSlug, setShownSlug] = useState<string | null>(null);
  useEffect(() => {
    setShownSlug((prev) => nextShownSlug(prev, urlSlug, slugState));
  }, [slugState, urlSlug]);
  // 주소에 담긴 `?project=`를 프로젝트 전환기에 반영한다.
  // 주소에 프로젝트가 없는 개인 화면(`/my/*`)은 이 값으로 프로젝트 맥락을 이어받는다.
  // 모든 보호 라우트가 이 셸을 거치므로 이 셸 한 곳에서 처리한다.
  useProjectParam();
  const { user, loadMe, error: authError, signOut } = useAuth();
  /*
    정지된 계정의 남은 세션에는 `/auth/me`가 403 `account_not_active`를 돌려준다.
    다시 시도해도 같은 응답이 오므로 재시도 대신 로그아웃을 안내한다.
  */
  const inactive = authError?.status === 403 && authError.detail === "account_not_active";
  const navigate = useNavigate();
  // 활동 중이면 세션을 백그라운드에서 자동 갱신한다. 활동이 없으면 세션이 만료되고 아래 모달이 다시 로그인을 받는다.
  useSessionKeepalive();
  const [collapsedPref, setCollapsedPref] = useState(
    () =>
      typeof localStorage !== "undefined" &&
      localStorage.getItem(COLLAPSED_KEY) === "1",
  );
  /*
    좁은 폭(`md`, 768px 미만)에서는 사용자가 고른 값과 관계없이 사이드바를 접는다.
    사이드바(`w-58`, 232px)를 편 채로 두면 375px 화면에서 본문 폭이 143px만 남아 어떤 화면도 쓸 수 없다.
    접으면 사이드바가 73.5px(`5.25rem`, 루트 14px 기준)라 본문 폭이 약 301px다. 768px 이상이면 펴도 본문 폭이 536px다.
    사용자가 고른 값(`collapsedPref`)은 덮어쓰지 않고 렌더할 때만 합쳐서, 창을 다시 넓히면 원래 고른 상태로 돌아온다.
  */
  const narrow = useMediaQuery("(max-width: 767px)");
  /*
    좁은 화면에서도 사이드바를 펼 수 있다.
    좁을 때는 콘텐츠 위에 덮어서 편다. 콘텐츠를 밀면 375px에서 본문 폭이 143px만 남아 읽을 수 없다.
    토글을 막으면 메뉴 이름을 보려고 창을 넓혀야 하므로 토글은 열어 둔다.
  */
  const [narrowOpen, setNarrowOpen] = useState(false);
  const collapsed = narrow ? !narrowOpen : collapsedPref;
  /** 좁은 화면에서 사이드바를 펼쳤으면 true. 참이면 스크림을 깔고 사이드바를 본문 위에 덮는다. */
  const overlay = narrow && narrowOpen;
  /*
    다른 화면으로 이동하면 사이드바를 닫는다. 덮은 채로 남으면 방금 연 화면을 사이드바가 가린다.
    Esc로도 닫는다(스크림을 눌러 닫는 것과 같은 동작).
  */
  const { pathname, search } = useLocation();
  useEffect(() => setNarrowOpen(false), [pathname]);
  useEffect(() => {
    if (!overlay) return;
    const onKey = (e: KeyboardEvent) =>
      e.key === "Escape" && setNarrowOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [overlay]);
  const [sessionExpired, setSessionExpired] = useState(false);

  // 세션 만료를 안내한다.
  // api의 401 이벤트를 받고, 로컬 만료 시각(exp)도 30초마다 미리 확인한다.
  // 만료 시각이 지났어도 활동 중이면 갱신을 먼저 시도하고, 서버가 거절했을 때만 안내한다(`accessLapsed`).
  // 갱신이 일시 오류로 실패했을 때 안내를 띄우면 서버가 잠깐 멈춘 것만으로 비밀번호를 다시 받게 된다.
  useEffect(() => {
    const onExpired = () => setSessionExpired(true);
    window.addEventListener("reborn:session-expired", onExpired);
    const t = window.setInterval(() => {
      void accessLapsed().then((lapsed) => {
        if (lapsed) setSessionExpired(true);
      });
    }, 30_000);
    return () => {
      window.removeEventListener("reborn:session-expired", onExpired);
      window.clearInterval(t);
    };
  }, []);

  useEffect(() => {
    if (isAuthed() && !user) void loadMe();
  }, [user, loadMe]);

  const toggleCollapsed = useCallback(() => {
    setCollapsedPref((prev) => {
      localStorage.setItem(COLLAPSED_KEY, prev ? "0" : "1");
      return !prev;
    });
  }, []);

  /*
    세션이 만료되면 셸과 본문을 그대로 둔 채 모달을 띄우고, 비밀번호만 다시 받아 같은 화면을 이어 간다.
    토큰이 없는 동안 셸의 조회가 받는 401은 전역 처리(queryClient)가 알리지 않고, 다시 로그인하면 모두 새로 불러온다.
    처음부터 토큰 없이 들어온 경우에만 돌아올 주소를 붙여 로그인 화면으로 보낸다.
    주의: 모달을 셸 대신 반환하면 `<Outlet/>`이 언마운트되어 워크벤치의 저장하지 않은 편집이 사라진다.
  */
  /*
    앱 시작 때 refresh 쿠키로 세션을 복구하지 못한 이유가 서버 일시 오류면 로그인 화면으로 보내지 않는다.
    쿠키는 아직 유효할 수 있어서, 로그인 화면으로 보내면 서버가 잠깐 멈춘 것만으로 비밀번호를 다시 받게 된다.
  */
  /*
    로그아웃으로 끝난 세션은 이 탭의 로그아웃이든 다른 탭의 신호든 돌아올 주소 없이 `/login`으로 보낸다(`authGate`).
    다른 탭의 신호는 `store/auth`가 사용자 상태를 비워 이 셸을 다시 그리게 한다.
  */
  const gate = authGate({
    authed: isAuthed(),
    sessionExpired,
    signedOut: sessionEndedBySignOut(),
    restoreUnavailable: restoreUnavailable(),
    pathname,
    search,
  });
  if (gate.kind === "restore-failed") {
    return <RestoreFailedPanel retryAfter={restoreRetryAfter()} />;
  }
  if (gate.kind === "login") {
    return <Navigate to={gate.to} replace />;
  }

  /*
    개인정보 수집·이용 동의 기록이 없는 계정에는 셸 대신 동의 화면을 그린다.
    서버가 동의 전 업무 요청을 403 `privacy_consent_required`로 막으므로 셸을 그려도 쓸 수 없다.
    동의하면 계정을 다시 받고, 403으로 실패했던 조회(프로젝트 목록 등)를 다시 부른다.
  */
  if (user && !user.privacy_consented_at) {
    return (
      <PrivacyConsentPage
        user={user}
        onConsented={async () => {
          await loadMe();
          void queryClient.invalidateQueries();
        }}
        onSignOut={() => {
          signOut();
          navigate("/login", { replace: true });
        }}
      />
    );
  }

  const body = layoutBody({ authFailed: !!authError && !user, slug: urlSlug, state: slugState, shownSlug });

  /*
    앰비언트 캔버스. 완전히 평평한 회색 배경은 배경을 정하지 않은 화면처럼 보인다.
    그래서 상단 양옆에 아주 옅은 빛을 깔아 카드가 떠 보일 바탕을 만든다.
    하루 종일 보는 도구라 배경을 고정(fixed)해 스크롤해도 빛이 움직이지 않게 한다.
  */
  return (
    <div className="relative min-h-screen bg-canvas">
      <div
        aria-hidden="true"
        /*
          셸 표식. 인쇄할 때는 뺀다(`styles/globals.css`).
          종이 바탕은 이미 흰색이라 인쇄하면 이 빛은 잉크만 소모한다.
        */
        data-shell-chrome=""
        className="pointer-events-none fixed inset-0 z-0 bg-canvas-ambient"
      />
      {/*
        스크림은 사이드바를 덮어서 편 동안만 둔다. 누르면 닫힌다(모달과 같은 동작).
        사이드바와 같은 z 값에 두고 DOM에서 사이드바보다 앞에 둔다.
        같은 z 값이면 뒤에 오는 형제가 위에 그려지므로 새 z 토큰이 필요 없다.
      */}
      {overlay && (
        <button
          type="button"
          aria-label="사이드바 닫기"
          onClick={() => setNarrowOpen(false)}
          className="fixed inset-0 z-sidebar bg-backdrop"
        />
      )}
      <AppSidebar collapsed={collapsed} />

      {/*
        주의: <main>과 콘텐츠 래퍼에 overflow-auto/scroll을 넣지 않는다. TopBar·표 thead의 sticky와 영상 상세의 lg:sticky 사이드바가 뷰포트 스크롤을 쓴다.
        주의: <main>에 z-index를 주지 않는다. `relative z-10`을 붙이면 main이 스태킹 컨텍스트가 되어 전체 화면 워크벤치(z=60)가 main의 z=10 안에 갇힌다.
        그러면 형제인 사이드바(z=30)가 워크벤치 위에 그려진다.
        main은 DOM 순서상 앰비언트(z-0) 뒤의 positioned 요소라서 z-index 없이도 앰비언트 위에 그려진다.
      */}
      <main
        className={cn(
          "relative transition-all duration-normal",
          overlay ? "ml-[5.25rem]" : collapsed ? "ml-[5.25rem]" : "ml-58",
        )}
      >
        <TopBar
          onToggleSidebar={
            narrow ? () => setNarrowOpen((o) => !o) : toggleCollapsed
          }
          sidebarCollapsed={collapsed}
        />
        <div className="mx-auto w-full max-w-[1360px] px-10 pb-16 pt-7">
          {/*
            URL의 `/p/:slug`가 보여 줄 프로젝트를 정한다.
            없는 slug는 `프로젝트 없음`으로 알린다.
            다른 프로젝트를 대신 보여 주면 사용자는 맞는 프로젝트를 보고 있다고 착각한다.
          */}
          {/*
            `loadMe()`가 실패하면 `user`가 null이다. 모든 보호 라우트의 본문인 이 위치에서 오류와 다시 시도 버튼을 보여 준다.
            주의: `RoleGuard`는 null `user`를 "인증 정보 로딩 대기"로 읽는다.
            `_layout`의 효과는 `[user, loadMe]`에 의존해서, 실패 뒤에도 `user`가 null이면 다시 실행되지 않는다.
            그래서 이 위치에서 실패를 처리하지 않으면 일시 오류가 영구 실패가 된다.
          */}
          {/*
            셸 본문의 막힘·실패는 `StatePanel` 한 모양으로 그린다.
            실패는 위험색 아이콘과 "다시 시도" 주 버튼, 없음은 먹색 아이콘과 목적지 버튼이다.
          */}
          {/* 활성이 아닌 프로젝트를 보고 있으면 본문 위에 프로젝트 상태를 표시한다. 본문을 그릴 때만 둔다. */}
          {body === "page" && <ProjectStatusNotice />}
          {body === "auth-error" && inactive ? (
            <StatePanel
              tone="bad"
              icon={<TriangleAlert size={18} aria-hidden="true" />}
              title="사용할 수 없는 계정"
              lead="정지되었거나 승인되지 않은 계정입니다."
              actions={<Button onClick={() => { signOut(); navigate("/login", { replace: true }); }}>로그아웃</Button>}
            />
          ) : body === "auth-error" ? (
            <StatePanel
              tone="bad"
              icon={<TriangleAlert size={18} aria-hidden="true" />}
              title="계정 정보 조회 실패"
              lead="메뉴와 권한은 계정 정보를 받은 뒤에 열립니다."
              actions={<Button onClick={() => void loadMe()}>다시 시도</Button>}
            />
          ) : body === "project-error" ? (
            <StatePanel
              tone="bad"
              icon={<TriangleAlert size={18} aria-hidden="true" />}
              title="프로젝트 목록 조회 실패"
              lead="주소의 프로젝트는 목록을 받은 뒤에 확인됩니다."
              actions={<Button onClick={retryProjects}>다시 시도</Button>}
            />
          ) : body === "unknown-project" ? (
            <StatePanel
              icon={<FolderX size={18} aria-hidden="true" />}
              title={`‘${urlSlug}’ 프로젝트 없음`}
              lead="주소가 틀렸거나 이 프로젝트의 멤버가 아닙니다."
              hint="참여가 필요하면 프로젝트 관리자에게 요청해주세요."
              // 사이드바를 접으면 전환기 이름이 안 보이므로 위치를 글로 안내하지 않고 갈 곳을 버튼으로 준다.
              actions={
                <Link to={PROJECT_LIST_LANDING.to} className={buttonClasses("primary")}>
                  {moveLabel(PROJECT_LIST_LANDING.label)}
                </Link>
              }
            />
          ) : body === "hold" ? (
            // 주소의 프로젝트로 맞출 때까지 본문을 미룬다. 모양은 `RoleGuard`의 프로젝트 골격과 같다.
            <PageSkeleton header panels={2} />
          ) : (
            <Outlet />
          )}
        </div>
      </main>

      {/*
        세션 만료 모달. 만료된 뒤에는 다시 로그인만 할 수 있다.
        작업하는 동안에는 `useSessionKeepalive`가 만료 전에 갱신하므로 자리를 비웠을 때만 만료된다.
        모달은 본문 위에 뜬다. 다시 로그인하면 같은 화면을 그대로 이어 간다.
      */}
      <SessionExpiredModal
        open={sessionExpired}
        email={user?.email ?? null}
        onRelogged={async () => {
          await loadMe();
          // 토큰 없이 실패한 조회를 새 토큰으로 다시 부른다. 화면의 로컬 상태는 그대로 남는다.
          void queryClient.invalidateQueries();
          setSessionExpired(false);
        }}
        onElsewhere={() => navigate(loginPath(pathname, search), { replace: true })}
      />
    </div>
  );
}

/** 푸터의 제출 버튼이 가리킬 폼 id. 이 창에 폼이 하나라 고정 문자열로 충분하다. */
const RELOGIN_FORM_ID = "relogin-form";

/**
 * 세션 만료 안내 모달을 그린다.
 * 본문을 내리지 않고 그 위에서 다시 로그인한다.
 *
 * 만료는 셸의 선제 감지(만료 시각이 지남)와 `api()`의 401(토큰을 지우고 이벤트를 보낸다) 두 경로로 감지한다.
 * 어느 경우든 본문(`<Outlet/>`)은 그대로 둔다.
 * 워크벤치의 저장하지 않은 편집이 메모리에 남아 있어야 같은 화면에서 이어서 작업할 수 있다.
 *
 * 그래서 이 모달에서 바로 로그인을 받고, 이메일은 지금 계정으로 고정한다.
 * 다른 계정으로 들어가면 이 화면을 이어 갈 수 없으므로 `다른 계정으로 로그인`은 돌아올 주소를 붙여 로그인 화면으로 보낸다.
 * 현재 비밀번호가 틀려도 모달이 닫히지 않도록 401을 오류로 받는다(`expect401`).
 *
 * `다시 로그인`과 `다른 계정으로 로그인`은 창의 `footer` 슬롯에 있다.
 * 본문 밖이라 제출 버튼이 `form` 속성으로 위 폼을 가리킨다.
 */
function SessionExpiredModal({
  open,
  email,
  onRelogged,
  onElsewhere,
}: {
  open: boolean;
  /**
   * 지금 계정의 이메일. 없으면(계정 정보를 받기 전에 세션이 끊긴 경우) 이메일 입력란을 연다.
   */
  email: string | null;
  onRelogged: () => Promise<void>;
  onElsewhere: () => void;
}) {
  const [typedEmail, setTypedEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; hint?: string } | null>(null);

  // 다시 열릴 때 지난 입력·실패를 남기지 않는다.
  useEffect(() => {
    if (!open) {
      setPassword("");
      setError(null);
    }
  }, [open]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await login({ email: email ?? typedEmail, password });
      setSession(r.session.access_token);
      await onRelogged();
    } catch (err) {
      setError(loginErrorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onElsewhere}
      /*
        배경 클릭과 Esc로는 닫지 않고 두 버튼 중 하나를 고르게 한다.
        닫기는 `다른 계정으로 로그인`과 같아 로그인 화면으로 이동하므로 저장하지 않은 편집이 사라진다.
      */
      dismissible={false}
      title="세션 만료"
      icon={<Timer size={15} aria-hidden="true" />}
      tone="neutral"
      /*
        액션 줄은 창(`footer`)이 그린다. 푸터는 본문 밖이라 제출 버튼이 `<form>` 밖에 놓인다.
        그래서 HTML의 `form` 속성으로 버튼이 속한 폼을 지정한다. 지정해야 Enter와 클릭이 폼을 제출한다.
      */
      footer={
        <>
          <Button type="button" variant="ghost" className="mr-auto" onClick={onElsewhere}>
            다른 계정으로 로그인
          </Button>
          <Button type="submit" form={RELOGIN_FORM_ID} disabled={busy || !password}>
            {busy ? "로그인 중…" : "다시 로그인"}
          </Button>
        </>
      }
    >
      <form id={RELOGIN_FORM_ID} onSubmit={submit} className="flex flex-col gap-4">
        {/* 결과(굵게)와 다음 할 일(작게)을 나눈다. */}
        <div>
          <p className="text-callout font-semibold text-label">로그인 세션이 만료되었습니다.</p>
          <p className="mt-2 text-footnote text-label-2">
            비밀번호를 다시 입력하면 이 화면에서 이어집니다. 저장하지 않은 편집도 그대로 남아 있습니다.
          </p>
        </div>
        {email ? (
          <div className="flex flex-col gap-1">
            <span className="text-caption font-semibold text-label-2">계정</span>
            <span className="font-mono text-callout text-label">{email}</span>
          </div>
        ) : (
          <div className="space-y-1.5">
            <Label htmlFor="relogin-email">이메일</Label>
            <Input
              id="relogin-email" type="email" autoComplete="email" required
              value={typedEmail} onChange={(e) => setTypedEmail(e.target.value)}
            />
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="relogin-password">비밀번호</Label>
          <Input
            id="relogin-password" type="password" autoComplete="current-password" required
            aria-invalid={error ? true : undefined}
            value={password} onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {error && (
          <div role="alert" className="rounded-control bg-bad-muted px-3 py-2">
            <p className="text-callout font-medium text-bad-text">{error.message}</p>
            {error.hint && <p className="mt-0.5 text-footnote text-bad-text">{error.hint}</p>}
          </div>
        )}
      </form>
    </Modal>
  );
}
