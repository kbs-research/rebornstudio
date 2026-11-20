import { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Wordmark } from "@/shared/ui/Wordmark";

import kpfLogo from "./kpf-logo.png";

/**
 * 인증 화면(로그인·비밀번호 재설정)의 공통 셸. 왼쪽 흑연색 패널, 오른쪽 폼이다.
 *
 * 왼쪽 패널은 테마와 무관하게 흑연색이다(`data-surface="graphite"`). 제품 이름과 무엇을 하는 곳인지 한 줄만 둔다.
 * 제품 설명·목업은 두지 않아 폼에 시선이 모이게 한다. 바탕은 호박색 빛과 클립 타임라인 그림으로 평면 흑연색과 구분한다.
 * 좁은 폭(lg 미만)에서는 왼쪽 패널을 숨기고 폼 위에 작은 워드마크를, 폼 아래에 동의 안내 링크를 둔다.
 * 지원 기관 표기(개발지원 + 한국언론진흥재단 로고)는 첫 화면 하단에 둔다. 넓은 폭은 왼쪽 패널 맨 아래, 좁은 폭은 폼 아래다.
 * `children`은 오른쪽 폼이며 자기 폭을 정한다.
 */
export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-canvas">
      <aside
        data-surface="graphite"
        className="hidden w-[520px] shrink-0 flex-col justify-between bg-auth-panel px-12 py-10 text-label lg:flex"
      >
        <Wordmark />
        <ClipTimeline />
        <div>
          {/* 소개 한 줄이라 `p`로 둔다. 페이지 제목(`h1`)은 오른쪽 폼에 있다.
              두 줄은 의미 단위로 끊는다. 폭에 맡기면 마지막 낱말 하나만 둘째 줄로 내려간다. */}
          <p className="text-display text-label">
            <span className="block">보도영상 학습 데이터</span>
            <span className="block">가공 플랫폼</span>
          </p>
          <Link
            to="/privacy"
            className="mt-8 inline-block text-caption text-label-3 underline underline-offset-4 hover:text-label-2"
          >
            개인정보 수집·이용 동의
          </Link>
          <SupportNotice className="mt-6" />
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col items-center justify-center px-4 py-12">
        <div className="mb-8 lg:hidden">
          <Wordmark />
        </div>
        {children}
        {/* 좁은 폭에서는 왼쪽 패널이 없다. 동의 안내는 로그인 전에 읽을 수 있어야 하므로 링크를 폼 아래에 둔다. */}
        <Link
          to="/privacy"
          className="mt-10 text-caption text-label-3 underline underline-offset-4 hover:text-label-2 lg:hidden"
        >
          개인정보 수집·이용 동의
        </Link>
        <SupportNotice className="mt-4 justify-center lg:hidden" />
      </main>
    </div>
  );
}

/** 화면 낭독기와 로고가 안 보이는 환경에 주는 문장. 지원 사업 협약의 표기 문구다. */
const SUPPORT_NOTICE = "본 시스템은 한국언론진흥재단의 지원을 받아 개발되었습니다.";

/**
 * 지원 기관 표기. 협약이 정한 "개발지원: 한국언론진흥재단 로고" 형식이다.
 *
 * 로고는 흰 바탕용이라 흑연색 패널과 다크 테마에서도 흰 받침(`bg-logo-plate`) 위에 둔다.
 * 최초 설정 화면도 셸을 따로 그리지만 미초기화 배포의 첫 화면이라 같은 표기를 쓴다.
 */
export function SupportNotice({ className = "" }: { className?: string }) {
  return (
    <p className={`flex items-center gap-3 text-caption text-label-3 ${className}`}>
      <span>개발지원</span>
      <span className="inline-flex rounded-control bg-logo-plate px-2 py-1">
        <img src={kpfLogo} alt={SUPPORT_NOTICE} className="h-7 w-auto" />
      </span>
    </p>
  );
}

/** 타임라인 눈금 수. 패널 폭 424를 8 간격으로 나눈 값이다. */
const TICKS = 53;

/** 영상 한 편을 잘라 낸 클립 구간(시작 x · 폭). 길이가 고르지 않아야 실제 편집 타임라인처럼 읽힌다. */
const CLIPS: ReadonlyArray<readonly [number, number]> = [
  [0, 58], [62, 34], [100, 92], [196, 46], [246, 70], [320, 28], [352, 72],
];

/** 클립마다 붙는 캡션 줄(시작 x · 폭). 클립보다 짧게 두어 글자 줄로 읽히게 한다. */
const CAPTIONS: ReadonlyArray<readonly [number, number]> = [
  [4, 40], [66, 22], [104, 70], [200, 30], [250, 52], [356, 50],
];

/**
 * 왼쪽 패널 가운데의 장식 그림. 눈금 · 영상 트랙 · 클립 트랙 · 캡션 트랙과 재생 위치로 이루어진 편집 타임라인이다.
 *
 * 정보를 담지 않는 그림이라 스크린리더에서 숨긴다. 색은 `currentColor`와 토큰 유틸로만 칠한다.
 */
function ClipTimeline() {
  return (
    <svg viewBox="0 0 424 132" className="w-full text-label-3" aria-hidden="true" focusable="false">
      {Array.from({ length: TICKS }, (_, i) => (
        <rect key={i} x={i * 8} y={i % 5 === 0 ? 0 : 6} width={1} height={i % 5 === 0 ? 14 : 8} fill="currentColor" opacity={0.35} />
      ))}
      <rect x={0} y={26} width={424} height={22} rx={4} fill="currentColor" opacity={0.14} />
      {CLIPS.map(([x, w]) => (
        <rect key={x} x={x} y={58} width={w} height={22} rx={4} className="fill-amber" opacity={0.85} />
      ))}
      {CAPTIONS.map(([x, w]) => (
        <rect key={x} x={x} y={92} width={w} height={6} rx={3} fill="currentColor" opacity={0.45} />
      ))}
      {CAPTIONS.map(([x, w]) => (
        <rect key={`b${x}`} x={x} y={104} width={Math.round(w * 0.6)} height={6} rx={3} fill="currentColor" opacity={0.25} />
      ))}
      <rect x={281} y={0} width={2} height={124} className="fill-amber" />
      <path d="M276 0h12l-6 8z" className="fill-amber" />
    </svg>
  );
}

