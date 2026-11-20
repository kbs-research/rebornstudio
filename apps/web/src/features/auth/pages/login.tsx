/**
 * 로그인 화면(`/login`). 영상 가공 플랫폼의 인증 진입점이다.
 * 셸은 `AuthShell`(왼쪽 흑연색 패널, 오른쪽 폼)이다. lg보다 좁으면 폼 한 열과 작은 워드마크만 보인다.
 *
 * 사이드바와 헤더가 없는 auth-only 레이아웃이다. 오른쪽은 카드 없이 폼 하나이고 입력란은 42px이다.
 */
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";

import { Button } from "@/shared/ui/Button";
import { Input } from "@/shared/ui/Input";
import { FormError } from "@/shared/ui/FormError";
import { Label } from "@/shared/ui/Label";
import { isAuthed, setSession } from "@/shared/api/auth";
import { type ErrorText, loginErrorText } from "@/shared/api/error_text";
import { queryClient } from "@/app/queryClient";
import { useAuth } from "@/store/auth";
import { useDocumentTitle } from "@/shared/hooks/useDocumentTitle";

import { fetchSetupStatus, login } from "../api";
import { safeNext } from "../lib/safe_next";
import { AuthShell } from "../components/AuthShell";
import { AUTH_LINK, AuthNotice } from "../components/AuthNotice";

interface Form {
  email: string;
  password: string;
}

/** 로그인 폼을 그린다. 로그인하면 `next` 주소나 홈으로 보낸다. */
export function LoginPage() {
  const { loadMe } = useAuth();
  const navigate = useNavigate();
  const { register, handleSubmit, formState } = useForm<Form>();
  const [error, setError] = useState<ErrorText | null>(null);
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  // 셸 밖 화면이라 탭 제목을 직접 올린다. 여러 탭 중 로그인 탭을 찾을 수 있다.
  useDocumentTitle("로그인");

  /*
    아직 계정이 없는 배포면 셋업 화면으로 보낸다.
    계정이 없는 시스템에서 로그인을 시도하면 "이메일 또는 비밀번호가 올바르지 않습니다"만 나와
    무엇을 해야 하는지 알 수 없으므로 로그인 화면에서 먼저 확인한다.
    조회가 실패하면 무시하고 로그인 화면을 그대로 둔다. 셋업이 필요한 상태라면 로그인도 되지 않는다.
  */
  useEffect(() => {
    if (isAuthed()) return;
    let alive = true;
    fetchSetupStatus()
      .then((r) => { if (alive && r.uninitialized) navigate("/setup", { replace: true }); })
      .catch(() => {});
    return () => { alive = false; };
  }, [navigate]);

  if (isAuthed()) return <Navigate to={next} replace />;

  const onSubmit = handleSubmit(async (data) => {
    setError(null);
    try {
      // 이 요청의 401은 "비밀번호가 틀렸다"는 뜻이다. 세션 만료로 읽지 않도록 오류로 받는다(`login`).
      const r = await login(data);
      setSession(r.session.access_token);
      // 직전 세션(만료 등)의 프로젝트·역할 캐시를 지우고 새 계정으로 다시 조회한다.
      queryClient.clear();
      await loadMe();
      // 로그인 화면은 방문 기록에 남기지 않는다(`replace`).
      // 남기면 뒤로 가기가 로그인 화면을 다시 열고,
      // 위의 가드가 곧바로 되돌려 보내 뒤로 가기가 동작하지 않는 것처럼 보인다.
      // 셋업 리다이렉트도 이미 `replace`다. 셸이 붙여 보낸 돌아올 주소가 있으면 그 주소로 간다.
      navigate(next, { replace: true });
    } catch (e) {
      // 429 두 종류, 401, 그 밖의 오류를 원인에 따라 나눠 보여 준다.
      // 원인을 알 수 없는 오류에는 원인을 지어내지 않는다.
      setError(loginErrorText(e));
    }
  });

  return (
    <AuthShell>
      <div className="w-full max-w-[400px]">
        {/*
          제목은 `로그인` 하나다. 제품 이름은 왼쪽 패널에 이미 있고, 부제는 두지 않는다.
          카드 없이 밝은 면 위에 폼 하나를 둔다.
        */}
        <h1 className="text-title-1 text-label">로그인</h1>

        {/*
          안내는 입력란보다 먼저 보이게 제목 바로 아래에 둔다. 굵은 결과와 작은 안내로 나눈다.
          셋업이 만드는 계정은 최고관리자다. `관리자`는 프로젝트 역할 이름이다.
        */}
        <div className="mt-5 space-y-3 empty:hidden">
          {params.get("setup") === "done" && !error && (
            <AuthNotice tone="ok" title="최고관리자를 만들었습니다" hint="방금 정한 비밀번호로 로그인합니다." />
          )}
          {params.get("setup") === "already" && !error && (
            <AuthNotice tone="note" title="최초 설정이 이미 끝났습니다" hint="이 배포에는 최고관리자가 있습니다. 계정으로 로그인합니다." />
          )}
          {error && <AuthNotice tone="bad" title={error.message} hint={error.hint} />}
        </div>

        <form onSubmit={onSubmit} className="mt-5 space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="email">이메일</Label>
            <Input
              id="email" type="email" autoComplete="email"
              placeholder="name@example.com"
              className="h-10.5"
              aria-invalid={formState.errors.email ? true : undefined}
              {...register("email", { required: true })}
            />
            <FormError error={formState.errors.email} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="password">비밀번호</Label>
            <Input
              id="password" type="password" autoComplete="current-password"
              className="h-10.5"
              aria-invalid={formState.errors.password ? true : undefined}
              {...register("password", { required: true })}
            />
            <FormError error={formState.errors.password} />
          </div>

          <Button type="submit" size="lg" className="h-10.5 w-full" disabled={formState.isSubmitting}>
            {formState.isSubmitting ? "로그인 중…" : "로그인"}
          </Button>
        </form>

        {/* 공개 가입이 없다. 계정은 최고관리자가 만들고 비밀번호 재설정 링크를 전달한다. */}
        <div className="mt-6 flex items-center justify-between gap-4">
          <p className="text-caption text-label-3">계정은 최고관리자가 만듭니다.</p>
          <Link to="/password-reset" className={AUTH_LINK}>비밀번호 재설정</Link>
        </div>
      </div>
    </AuthShell>
  );
}
