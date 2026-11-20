/**
 * 개인정보 수집·이용 동의 화면. 동의 기록이 없는 계정이 로그인하면 보호 셸(`app/_layout.tsx`)이 셸 대신 그린다.
 *
 * 최고관리자가 만든 계정은 동의 기록 없이 생기므로 첫 로그인에서 여기로 온다.
 * 서버는 동의 전 업무 요청을 403 `privacy_consent_required`로 막고, 현재 사용자 정보 조회와 동의 기록만 허용한다.
 * 그래서 이 화면은 사이드바·상단바 없이 인증 셸 안에 그린다. 셸을 그리면 셸의 조회가 모두 403이 된다.
 *
 * 동의하기 전에 수집 항목과 보유 기간을 같은 화면에 보여 준다. 표는 안내 화면(`/privacy`)과 같다(`lib/privacy_terms`).
 * 동의는 필수 동의 하나만 받는다.
 * 동의하지 않는 사람에게는 로그아웃과 계정 삭제 요청을 안내한다. 필수 동의만 따로 거두는 기능은 없다.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { ExternalLink } from "lucide-react";

import { Button } from "@/shared/ui/Button";
import { Checkbox } from "@/shared/ui/Checkbox";
import { Chip } from "@/shared/ui/Chip";
import { type ApiError, type UserDTO } from "@/shared/api/client";
import { apiErrorText, type ErrorText } from "@/shared/api/error_text";
import { useDocumentTitle } from "@/shared/hooks/useDocumentTitle";

import { consentPrivacy } from "../api";
import { AuthShell } from "../components/AuthShell";
import { AUTH_LINK, AuthNotice } from "../components/AuthNotice";
import {
  COLLECTED,
  KIND_TONE,
  REQUIRED_CONSENT_LABEL,
  USE_END_NOTE,
} from "../lib/privacy_terms";

/**
 * 동의 화면을 그린다. 동의를 기록하면 `onConsented`를 부르고, 로그아웃을 누르면 `onSignOut`을 부른다.
 *
 * 세션이 끊겨 401이 오면 다시 로그인하도록 `onSignOut`으로 보낸다. 이 화면에는 세션 만료 창이 없다.
 */
export function PrivacyConsentPage({ user, onConsented, onSignOut }: {
  user: UserDTO;
  onConsented: () => Promise<void>;
  onSignOut: () => void;
}) {
  useDocumentTitle("개인정보 수집·이용 동의");
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorText | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await consentPrivacy();
      await onConsented();
    } catch (e) {
      if ((e as ApiError).status === 401) {
        onSignOut();
        return;
      }
      setError(apiErrorText(e, "동의 기록 실패"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthShell>
      <div className="w-full max-w-[440px]">
        <h1 className="text-title-1 text-label">개인정보 수집·이용 동의</h1>
        <p className="mt-2 text-callout text-label-2">RebornStudio를 이용하려면 개인정보 수집·이용 동의가 필요합니다.</p>

        {/* 수집 항목과 보유 기간. 이용 목적까지 담은 전체 고지는 링크로 연다. */}
        <dl className="mt-6 divide-y divide-separator rounded-field border border-separator-strong bg-panel">
          {COLLECTED.map((r) => (
            <div key={r.kind} className="px-3.5 py-3">
              <dt>
                <Chip tone={KIND_TONE[r.kind] ?? "neutral"}>{r.kind}</Chip>
              </dt>
              <dd className="mt-1.5 text-caption text-label-2">{r.items}</dd>
              <dd className="mt-0.5 text-caption text-label-3">보유 기간 · {r.period}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-2 text-caption text-label-3">{USE_END_NOTE}</p>
        <Link
          to="/privacy"
          target="_blank"
          className="mt-2 inline-flex items-center gap-1 text-caption font-medium text-label underline underline-offset-4"
        >
          전체 내용 보기 <ExternalLink size={12} aria-hidden="true" />
        </Link>

        <div className="mt-5 space-y-4">
          <Checkbox
            id="privacy_consent"
            checked={agreed}
            onChange={setAgreed}
            label={<span className="font-semibold">{REQUIRED_CONSENT_LABEL}</span>}
          />

          {error && <AuthNotice tone="bad" title={error.message} hint={error.hint} />}

          <div>
            <Button size="lg" className="h-10.5 w-full" disabled={!agreed || busy} onClick={() => void submit()}>
              {busy ? "기록 중…" : "동의"}
            </Button>
            {!agreed && <p className="mt-2 text-center text-caption text-label-3">필수 항목에 동의해야 진행합니다.</p>}
          </div>
        </div>

        {/* 동의하지 않는 사람이 나갈 곳. 필수 동의만 거두는 기능은 없어 계정 삭제 요청으로 안내한다. */}
        <div className="mt-8 flex items-start justify-between gap-4 border-t border-separator pt-4">
          <div className="min-w-0">
            <p className="truncate font-mono text-caption text-label-2">{user.email}</p>
            <p className="mt-0.5 text-caption text-label-3">계정 삭제는 최고관리자에게 요청합니다.</p>
          </div>
          <button type="button" onClick={onSignOut} className={AUTH_LINK}>로그아웃</button>
        </div>
      </div>
    </AuthShell>
  );
}
