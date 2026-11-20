/**
 * 비밀번호 재설정 화면(`/password-reset`).
 * 인증 셸 오른쪽에 카드 없이 폼 하나를 둔다.
 *
 * 최고관리자가 발급한 링크(`#token=…`)로 들어와 새 비밀번호를 정한다.
 * 조각으로 바꾸기 전에 발급한 `?token=…` 링크도 읽고, 읽은 뒤에는 주소창에서 토큰을 지운다.
 * 토큰을 가진 사람만 이 화면에서 비밀번호를 정할 수 있다.
 * 토큰이 없으면 어디서 받는지 안내한다. 지금 토큰을 받는 방법은 관리자 발급 하나뿐이다.
 * 메일 발송 기능이 없어 이메일로 링크를 요청하는 단계를 두지 않는다.
 */
import { useMutation, useQuery } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Check } from "lucide-react";

import { Button, buttonClasses } from "@/shared/ui/Button";
import { Input } from "@/shared/ui/Input";
import { Label } from "@/shared/ui/Label";
import { apiErrorText } from "@/shared/api/error_text";
import { fmtEventTime } from "@/features/audit";
import { useDocumentTitle } from "@/shared/hooks/useDocumentTitle";

import { authKeys, checkPasswordReset, confirmPasswordReset } from "../api";
import { AuthShell } from "../components/AuthShell";
import { AUTH_LINK, AuthNotice } from "../components/AuthNotice";
import { meetsPasswordPolicy, PasswordRules } from "../lib/password_policy";
import { addressWithoutToken, resetTokenFrom, tokenFromLocation } from "../lib/reset_token";

/**
 * 쓸 수 없는 링크의 제목. 이유는 서버가 준다(`PasswordResetStatus.reason`).
 * 만료와 사용을 가르면 받은 사람이 "다시 발급" 외에 "이미 바꿨는지" 스스로 확인할 수 있다.
 */
const DEAD_LINK_TITLE: Record<"expired" | "used" | "unknown", string> = {
  expired: "유효 시간이 지난 링크",
  used: "이미 비밀번호를 바꾼 링크",
  unknown: "확인할 수 없는 링크",
};

/** 재설정 링크의 토큰과 새 비밀번호를 받아 비밀번호를 재설정한다. */
export function PasswordResetPage() {
  // 링크로 들어오면 토큰이 주소에 들어 있다. 없으면 받은 링크를 붙여 넣을 수 있게 입력란을 둔다.
  // 관리자가 메신저로 링크를 전달하다 주소가 잘리는 경우가 있다.
  // 입력란은 링크 전체를 받고, 보낼 때 토큰만 꺼낸다(`lib/reset_token`).
  // 링크는 토큰을 주소 조각(`#token=`)에 싣는다. 조각은 서버로 가지 않아 접근 로그에 남지 않는다.
  const location = useLocation();
  const [linkToken] = useState(() => tokenFromLocation(location.search, location.hash));
  const [pasted, setPasted] = useState(linkToken ?? "");
  const token = resetTokenFrom(pasted);
  const fromLink = !!linkToken;
  // 읽은 토큰은 주소창과 방문 기록에서 바로 지운다. 옛 쿼리 링크도 같은 방법으로 지운다.
  useEffect(() => {
    if (!linkToken) return;
    const { pathname, search, hash } = window.location;
    window.history.replaceState(window.history.state, "", addressWithoutToken(pathname, search, hash));
  }, [linkToken]);
  const [newPw, setNewPw] = useState("");
  const [newPw2, setNewPw2] = useState("");
  const [done, setDone] = useState(false);
  // 셸 밖 화면이라 탭 제목을 직접 올린다.
  useDocumentTitle("비밀번호 재설정");
  /*
    확인 입력이 새 비밀번호와 같은지 판정한다. 확인 입력을 시작한 뒤에만 다르다고 알린다.
    확인 없이 오타 난 비밀번호로 재설정하면 링크를 다시 받아야 한다.
  */
  const matches = newPw2 === newPw;

  /*
    새 비밀번호를 받기 전에 링크를 아직 쓸 수 있는지 확인한다. 그래야 만료된 링크로 비밀번호를 두 번 입력한 뒤에 실패를 알게 되는 일이 없다.
    토큰이 없으면 빈 값으로 조회해 유효 시간만 받는다. 유효 시간은 서버 값을 쓰고 화면에 30분을 고정해 적지 않는다.
    토큰은 주소 대신 본문으로 보낸다. 이 조회가 실패하면 확정 단계에서 서버가 판정한다.
  */
  const check = useQuery({
    queryKey: authKeys.passwordResetCheck(token),
    queryFn: () => checkPasswordReset(token),
    retry: false,
    staleTime: 30_000,
    meta: { handled: true },
  });
  const linkDead = !!token && check.data?.valid === false;
  const expiresAt = token && check.data?.valid ? check.data.expires_at : null;

  // 실패는 이 화면이 직접 그린다. 전역 토스트와 겹치지 않게 `meta.handled`를 켠다.
  const confirmMut = useMutation({
    mutationFn: () => confirmPasswordReset(token, newPw),
    onSuccess: () => setDone(true),
    meta: { handled: true },
  });

  function onConfirm(e: FormEvent) { e.preventDefault(); confirmMut.mutate(); }

  const blocked = !token
    ? "재설정 링크를 붙여 넣어야 바꿉니다."
    : linkDead
      ? "쓸 수 있는 링크가 있어야 바꿉니다."
      : !meetsPasswordPolicy(newPw)
      ? "비밀번호 규칙을 모두 충족해야 바꿉니다."
      : !matches
        ? "두 비밀번호가 같아야 바꿉니다."
        : null;

  return (
    <AuthShell>
      <div className="w-full max-w-[400px]">
        {/* 제목은 하나다. 완료 뒤에는 결과 이름으로 바뀐다. 새 비밀번호를 정하는 화면이라는 것은 입력란으로 알 수 있다. */}
        {done && (
          <span className="mb-4 grid size-11 place-items-center rounded-capsule bg-ok-muted text-ok-text">
            <Check size={18} aria-hidden="true" />
          </span>
        )}
        <h1 className="text-title-1 text-label">{done ? "비밀번호 재설정 완료" : "비밀번호 재설정"}</h1>
        {/* 링크가 유효하면 언제까지인지 제목 아래에 적는다. 자정을 넘길 수 있어 날짜와 시각을 같이 쓴다. */}
        {!done && expiresAt && (
          <p className="mt-1.5 text-caption tabular-nums text-label-3">링크 만료 {fmtEventTime(expiresAt).slice(0, 16)}</p>
        )}
        {done ? (
          <div>
            <p className="mt-2 text-callout text-label-2">새 비밀번호로 로그인합니다.</p>
            <Link to="/login" className={buttonClasses("primary", "lg", "mt-6 h-10.5 w-full")}>로그인</Link>
          </div>
        ) : (
          <>
            <form onSubmit={onConfirm} className="mt-5 space-y-4">
              {/*
                링크 없이 들어온 사람에게 어디서 받는지만 안내한다. 지금 링크를 받는 방법은 최고관리자 발급 하나뿐이다.
                유효 시간은 링크 상태 확인이 주는 서버 값을 쓴다. 받기 전에는 적지 않는다.
                링크로 들어왔으면 입력란을 그리지 않는다. 토큰은 이미 주소에 있다.
              */}
              {!fromLink && (
                <>
                  <AuthNotice
                    tone="note"
                    title="재설정 링크가 필요합니다"
                    // 두 사실을 줄로 나눈다. 한 문단으로 이으면 좁은 상자에서 문장 가운데가 끊긴다.
                    hint={
                      <>
                        <span className="block">최고관리자에게 링크를 요청합니다.</span>
                        {check.data && (
                          <span className="block tabular-nums">발급 후 {check.data.valid_minutes}분 뒤 만료됩니다.</span>
                        )}
                      </>
                    }
                  />
                  <div className="space-y-1.5">
                    {/* 같은 것을 `링크`와 `코드` 두 이름으로 부르지 않는다. */}
                    <Label htmlFor="token">재설정 링크</Label>
                    <Input id="token" value={pasted} onChange={(e) => setPasted(e.target.value)}
                      placeholder="받은 링크 붙여넣기" required autoComplete="off"
                      spellCheck={false} className="h-10.5 font-mono text-footnote placeholder:font-sans" />
                  </div>
                </>
              )}
              {/* 쓸 수 없는 이유를 서버가 준다(만료·사용·모름). 받은 사람이 무엇을 요청할지 알 수 있다. */}
              {linkDead && (
                <AuthNotice tone="bad" title={DEAD_LINK_TITLE[check.data?.reason ?? "unknown"]} hint="최고관리자에게 새 링크를 요청합니다." />
              )}
              {!(fromLink && linkDead) && (<>
              <div className="space-y-1.5">
                <Label htmlFor="newpw">새 비밀번호</Label>
                <Input id="newpw" type="password" value={newPw} autoComplete="new-password"
                  className="h-10.5" aria-describedby="newpw-rules"
                  onChange={(e) => setNewPw(e.target.value)} required />
                {/* 규칙은 `lib/password_policy` 한 곳에서 정한다. 입력 전에도 보인다. */}
                <div id="newpw-rules">
                  <PasswordRules value={newPw} always />
                </div>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="newpw2">새 비밀번호 확인</Label>
                <Input id="newpw2" type="password" value={newPw2} autoComplete="new-password" placeholder="한 번 더 입력"
                  className="h-10.5"
                  aria-invalid={newPw2 !== "" && !matches ? true : undefined}
                  aria-describedby={newPw2 !== "" && !matches ? "newpw2-error" : undefined}
                  onChange={(e) => setNewPw2(e.target.value)} required />
                {newPw2 !== "" && !matches && (
                  <p id="newpw2-error" role="alert" className="text-footnote font-medium text-bad-text">비밀번호가 서로 다릅니다.</p>
                )}
              </div>
              </>)}
              {/* 만료된 토큰(400)과 정책 위반(422)을 구분해 보여 준다. */}
              {confirmMut.error && (() => {
                const t = apiErrorText(confirmMut.error, "재설정 실패");
                return <AuthNotice tone="bad" title={t.message} hint={t.hint} />;
              })()}
              {!(fromLink && linkDead) && <div>
                <Button type="submit" size="lg" className="h-10.5 w-full" disabled={confirmMut.isPending || !!blocked}>
                  {confirmMut.isPending ? "재설정 중…" : "비밀번호 재설정"}
                </Button>
                {blocked && (newPw || newPw2 || pasted) && (
                  <p className="mt-2 text-center text-caption text-label-3">{blocked}</p>
                )}
              </div>}
            </form>
            <div className="mt-6">
              {/* 화살표 글자 대신 어디로 가는지 글자로 쓴다. */}
              <Link to="/login" className={AUTH_LINK}>로그인으로 돌아가기</Link>
            </div>
          </>
        )}
      </div>
    </AuthShell>
  );
}
