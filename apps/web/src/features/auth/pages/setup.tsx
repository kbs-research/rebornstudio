/**
 * 최초 설정 화면(`/setup`). 배포 직후 첫 관리자를 만든다.
 *
 * 계정이 하나도 없는 시스템에 처음 들어가는 방법은 로그인 화면으로 해결할 수 없어 이 화면이 맡는다.
 * 마이그레이션으로 계정을 미리 넣으면 저장소에 적힌 비밀번호가 인터넷에 열린 서버에 남으므로 미리 넣지 않는다.
 *
 * 셋업 토큰과 첫 최고관리자 정보를 한 폼으로 받는다.
 * 셋업 토큰은 서버 로그를 읽을 수 있는 사람만 통과시킨다.
 * 공개 IP를 먼저 찾은 사람이 관리자가 되는 것을 막는 유일한 장치다.
 * 토큰은 서버가 관리자를 만들 때(`POST /setup/complete`) 검사한다.
 *
 * 연동 진단은 이 화면에 두지 않는다. 인증 없는 화면에 버킷 이름 같은 내부 정보를 보이지 않기 위해서다.
 * 설치 뒤 연동 상태는 최고관리자가 로그인해 플랫폼 개요에서 확인한다.
 *
 * 첫 최고관리자도 다른 계정과 같이 개인정보 수집·이용 동의가 필수다. 체크하기 전에는 만들기 버튼이 꺼진다.
 */
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate } from "react-router-dom";
import { Check, LoaderCircle } from "lucide-react";

import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { Checkbox } from "@/shared/ui/Checkbox";
import { Input } from "@/shared/ui/Input";
import { FormError } from "@/shared/ui/FormError";
import { Label } from "@/shared/ui/Label";
import { Wordmark } from "@/shared/ui/Wordmark";
import { apiErrorText, type ErrorText } from "@/shared/api/error_text";
import { useDocumentTitle } from "@/shared/hooks/useDocumentTitle";
import { cn } from "@/shared/lib/cn";

import { completeSetup, fetchSetupStatus } from "../api";
import { AuthNotice } from "../components/AuthNotice";
import { SupportNotice } from "../components/AuthShell";
import { meetsPasswordPolicy, PasswordRules } from "../lib/password_policy";
import { REQUIRED_CONSENT_LABEL } from "../lib/privacy_terms";

/**
 * 셋업 실패 문구를 만든다.
 *
 * 서버가 준 문장(초기화됨, 이메일 중복, 비밀번호 정책)은 그대로 쓰고,
 * 오류 코드는 문구 사전으로 바꾼다.
 * 403은 원인이 분명하므로 확인할 곳까지 안내한다.
 * 409 두 종류(이미 초기화, 쓰이는 이메일)를 한 문구로 합치면 무엇을 고칠지 알 수 없다.
 */
function setupErrorText(e: unknown, fallback: string): ErrorText {
  const t = apiErrorText(e, fallback);
  if ((e as { status?: number })?.status === 403) {
    return { message: t.message, hint: "서버 로그의 셋업 토큰을 다시 확인해주세요." };
  }
  return t;
}

interface Form {
  token: string;
  email: string;
  display_name: string;
  password: string;
  password2: string;
}

/** 최초 설정 화면을 그린다. 셋업 토큰과 첫 최고관리자 정보를 한 폼으로 받는다. */
export function SetupPage() {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorText | null>(null);
  // 개인정보 수집·이용 동의(필수). 서버도 동의 없는 요청을 422로 거절한다.
  const [consent, setConsent] = useState(false);
  const { register, handleSubmit, formState, watch } = useForm<Form>();
  const pwd = watch("password") ?? "";
  const token = watch("token") ?? "";
  // 셋업 필요 여부. 확인 전(`null`), 셋업 필요(`true`), 이미 완료(`false`) 중 하나다.
  const [uninitialized, setUninitialized] = useState<boolean | null>(null);
  // 셸 밖 화면이라 탭 제목을 직접 올린다.
  useDocumentTitle("최초 설정");

  /*
    화면에 들어올 때 셋업이 필요한지 서버에 조회한다.
    `/setup/status`는 인증이 필요 없고 `{uninitialized, token_required}`만 돌려준다.
    조회하지 않고 늘 "아직 계정이 없습니다"라고 안내하면, 이미 초기화된 배포에서도 사용자가
    셋업 토큰을 찾아 입력하고 만들기를 누른 뒤에야 409로 알게 된다.
  */
  useEffect(() => {
    let alive = true;
    fetchSetupStatus()
      .then((r) => alive && setUninitialized(r.uninitialized))
      // 조회에 실패하면 막지 않는다. 셋업 가능 여부는 서버가 최종 판정한다(403/409).
      .catch(() => alive && setUninitialized(true));
    return () => { alive = false; };
  }, []);

  // 이미 설정이 끝났으면 로그인으로 보내고, 로그인 화면이 이동한 이유를 안내한다.
  useEffect(() => {
    if (uninitialized === false) navigate("/login?setup=already", { replace: true });
  }, [uninitialized, navigate]);

  const onSubmit = handleSubmit(async (d) => {
    if (d.password !== d.password2) {
      setError({ message: "비밀번호가 서로 다릅니다" });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await completeSetup({
        email: d.email, display_name: d.display_name, password: d.password, token: d.token,
        privacy_consent: consent,
      });
      // 셋업을 마쳐도 세션을 만들지 않는다.
      // 방금 정한 비밀번호로 한 번 로그인해 보는 것이 계정을
      // 실제로 쓸 수 있는지 확인하는 가장 짧은 방법이다.
      navigate("/login?setup=done", { replace: true });
    } catch (e) {
      setError(setupErrorText(e, "최고관리자 생성 실패"));
    } finally {
      setBusy(false);
    }
  });

  /** 실패 안내. 굵은 결과와 작은 안내로 나눈다. 다른 인증 화면과 같은 상자다. */
  const errorBox = error && <AuthNotice tone="bad" title={error.message} hint={error.hint} />;
  // 현재 단계 번호. 셋업 토큰을 입력하면 둘째 단계(최고관리자 만들기)다.
  const step = token.trim() ? 2 : 1;

  // 서버 응답이 오기 전에는 폼을 그리지 않는다. 틀린 안내가 잠깐이라도 보이지 않게 한다.
  if (uninitialized !== true) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-canvas px-6">
        <p className="text-footnote text-label-3">불러오는 중…</p>
      </main>
    );
  }

  return (
    <div className="flex min-h-screen bg-canvas">
      {/*
        왼쪽 흑연색 패널에 두 단계를 세로로 둔다. 인증 화면과 같은 레이아웃이다(`AuthShell`).
        이 화면만 패널 안에 단계와 토큰 찾는 명령을 두어 셸을 따로 그린다.
      */}
      <aside
        data-surface="graphite"
        className="hidden w-[520px] shrink-0 flex-col justify-between gap-10 bg-canvas px-12 py-10 lg:flex"
      >
        <Wordmark />
        <div>
          {/* 제목은 화면 폭에 따라 패널과 폼 위 중 한 곳에 보인다. 개요의 `h1`은 본문의 숨은 제목 하나다. */}
          <p aria-hidden="true" className="text-title-1 text-label">최초 설정</p>
          {/* 이 화면이 만드는 계정은 최고관리자다. `관리자`는 프로젝트 역할 이름이다. */}
          <p className="mt-3 text-callout text-label-2">
            이 배포에는 아직 계정이 없습니다. 서버 로그를 볼 수 있는 사람이 첫 최고관리자를 만듭니다.
          </p>
          <SetupSteps current={step} />
        </div>
        <div>
          <TokenCommands />
          {/* 미초기화 배포의 첫 화면이라 인증 셸과 같은 자리에 지원 기관 표기를 둔다. */}
          <SupportNotice className="mt-6" />
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col items-center justify-center px-4 py-12">
        <div className="w-full max-w-[680px] space-y-4">
          <h1 className="sr-only">최초 설정</h1>
          {/* 좁은 폭(lg 미만)에서는 왼쪽 패널이 없으므로 제목·단계·명령을 폼 위에 둔다. */}
          <div className="space-y-4 lg:hidden">
            <Wordmark />
            <p aria-hidden="true" className="text-title-1 text-label">최초 설정</p>
            <SetupSteps current={step} />
          </div>

          {/* 셋업 토큰과 관리자 정보를 한 폼으로 보낸다. 토큰은 서버가 관리자를 만들 때 검사한다. */}
          <form onSubmit={onSubmit} className="space-y-4">
            {/* 1) 토큰. 서버 로그를 읽었다는 증명이다. */}
            <Card as="section" aria-labelledby="token-title" className="p-5">
              <h2 id="token-title" className="text-headline text-label">셋업 토큰</h2>
              <p className="mt-1.5 text-footnote text-label-2">
                서버 로그에 출력된 값입니다. 이 값을 확인한 사람만 첫 최고관리자를 만듭니다.
              </p>
              <div className="mt-4 space-y-1.5">
                <Label htmlFor="token">셋업 토큰</Label>
                <Input
                  id="token"
                  placeholder="서버 로그에 출력된 값"
                  autoComplete="off"
                  spellCheck={false}
                  aria-invalid={formState.errors.token ? true : undefined}
                  // 입력란 안내 문구는 한글이라 본문 글꼴로 그린다. 고정폭이면 글자 사이가 벌어진다. 입력한 값만 고정폭이다.
                  className="font-mono placeholder:font-sans"
                  {...register("token", { required: true })}
                />
                <FormError error={formState.errors.token} />
              </div>
              <div className="mt-4 lg:hidden"><TokenCommands /></div>
            </Card>

            {/* 2) 최고관리자. 입력란 넷을 두 열로 둔다. */}
            <Card as="section" aria-labelledby="admin-title">
              <h2 id="admin-title" className="border-b border-separator px-5 py-3.5 text-headline text-label">첫 최고관리자</h2>
              <div className="space-y-4 p-5">
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor="email">이메일</Label>
                    <Input id="email" type="email" autoComplete="username"
                      aria-invalid={formState.errors.email ? true : undefined}
                      {...register("email", { required: true })} />
                    <FormError error={formState.errors.email} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="display_name">이름</Label>
                    <Input id="display_name"
                      aria-invalid={formState.errors.display_name ? true : undefined}
                      {...register("display_name", { required: true })} />
                    <FormError error={formState.errors.display_name} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="password">비밀번호</Label>
                    <Input id="password" type="password" autoComplete="new-password"
                      aria-invalid={formState.errors.password ? true : undefined}
                      {...register("password", { required: true })} />
                    <FormError error={formState.errors.password} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="password2">비밀번호 확인</Label>
                    <Input id="password2" type="password" autoComplete="new-password"
                      aria-invalid={formState.errors.password2 ? true : undefined}
                      {...register("password2", { required: true })} />
                    <FormError error={formState.errors.password2} />
                  </div>
                </div>
                {/*
                  `내 계정`·재설정 화면과 같은 비밀번호 규칙을 쓴다. 규칙은 `lib/password_policy` 한 곳에서 정하고
                  서버의 `validate_password_policy`와 같은 식이다. 권한이 가장 큰 계정이 가장 약한 비밀번호를 쓰지 않게 한다.
                */}
                <PasswordRules value={pwd} always />
                {/* `내용 보기` 링크는 체크 라벨 밖에 둔다. 라벨 안에 두면 링크를 눌러도 체크가 바뀐다. */}
                <div className="rounded-control bg-canvas-2 px-3.5 py-3">
                  <Checkbox
                    id="privacy_consent"
                    checked={consent}
                    onChange={setConsent}
                    label={<span className="font-semibold text-label">{REQUIRED_CONSENT_LABEL}</span>}
                  />
                  <p className="mt-1 pl-6 text-caption text-label-2">
                    동의하지 않으면 최고관리자를 만들 수 없습니다.{" "}
                    <Link to="/privacy" target="_blank" className="font-medium text-label underline underline-offset-4">내용 보기</Link>
                  </p>
                </div>
                {errorBox}
                <div className="flex justify-end">
                  <Button
                    type="submit"
                    size="lg"
                    disabled={busy || formState.isSubmitting || !token.trim() || !watch("email")
                      || !meetsPasswordPolicy(pwd) || !consent}
                  >
                    {busy && <LoaderCircle size={14} className="animate-spin" aria-hidden="true" />}
                    최고관리자 만들기
                  </Button>
                </div>
              </div>
            </Card>
          </form>
        </div>
        {/* 좁은 폭에서는 왼쪽 패널이 없으므로 지원 기관 표기를 폼 아래에 둔다. */}
        <SupportNotice className="mt-10 justify-center lg:hidden" />
      </main>
    </div>
  );
}

/**
 * 셋업 토큰을 찾는 명령을 보여 준다. 문서를 찾아보게 하지 않고 화면에서 안내한다.
 * 환경을 감지하지 않고 두 명령을 모두 보여 준다. 화면이 스스로 판정하면(빌드 모드, 호스트명) 틀릴 수 있다.
 * `dc.sh`는 배포 디렉터리(`APP_DIR`)의 compose를 부르는 서버용 래퍼라 로컬에서는 동작하지 않는다.
 * 로컬 명령은 compose 프로젝트 이름(`local`)으로 부르므로 컨테이너 이름과 실행 위치에 기대지 않는다.
 */
function TokenCommands() {
  return (
    <div>
      <p className="text-caption text-label-3">셋업 토큰 찾는 곳</p>
      <dl className="mt-2 space-y-1.5">
        {[
          ["서버", "infra/deploy/dc.sh logs api | grep '셋업 토큰'"],
          ["로컬", "docker compose -p essential logs api | grep '셋업 토큰'"],
        ].map(([where, cmd]) => (
          <div key={where} className="flex gap-2">
            <dt className="shrink-0 text-caption text-label-3">{where}</dt>
            <dd className="min-w-0">
              {/* 좁은 패널에서 잘리지 않게 줄을 바꾼다. 복사할 때는 한 줄로 붙는다. */}
              <code className="break-all font-mono text-caption text-label-2">{cmd}</code>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/**
 * 최초 설정의 두 단계와 지금 위치를 세로로 보여 준다. 끝난 단계는 확인 표시, 지금 단계만 호박색 테두리다.
 * 흑연색 패널 위에서도 같은 유틸이 패널에 맞는 색으로 그려진다.
 */
function SetupSteps({ current }: { current: number }) {
  const steps = [
    { label: "셋업 토큰 입력", sub: "서버 로그의 값" },
    { label: "첫 최고관리자 만들기", sub: "이메일 · 이름 · 비밀번호 · 동의" },
  ];
  // 토큰을 입력하면 첫 단계가 끝난다. 토큰은 만들기를 누를 때 서버가 검사한다.
  const doneUntil = current - 1;
  return (
    <ol aria-label="최초 설정 단계" className="mt-8 space-y-5">
      {steps.map((st, i) => {
        const n = i + 1;
        const done = n <= doneUntil;
        const now = n === current;
        return (
          <li key={st.label} className="flex gap-3" aria-current={now ? "step" : undefined}>
            <span
              className={cn(
                "grid size-6 shrink-0 place-items-center rounded-capsule text-caption font-semibold tabular-nums",
                done && "bg-label-2 text-canvas",
                now && "border-2 border-amber text-amber-text",
                !done && !now && "border border-separator-strong text-label-3",
              )}
            >
              {done ? <Check size={12} strokeWidth={3} aria-hidden="true" /> : n}
            </span>
            <span className="min-w-0">
              <span className={cn("block text-body", now || done ? "font-semibold text-label" : "text-label-3")}>
                {st.label}
                {done && <span className="sr-only"> 완료</span>}
              </span>
              <span className="mt-0.5 block text-caption text-label-3">{st.sub}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
