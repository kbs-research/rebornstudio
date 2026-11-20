/**
 * 개인정보 수집·이용 동의 안내 화면(`/privacy`).
 * 첫 로그인 전에 확인할 수 있도록 로그인 없이 열람한다.
 *
 * 표준 고지 4요소(수집 항목, 이용 목적, 보유·이용 기간, 동의 거부권과 불이익)를 담는다.
 * 필수 동의는 이용의 조건이고 선택 항목은 받지 않는다. 최초 설정·동의 화면과 내 계정 화면이 이 페이지로 링크한다.
 * 동의 시각은 users.privacy_consented_at에 기록한다.
 * 수집 항목 표는 동의 화면과 같이 쓴다(`lib/privacy_terms`).
 */
import { Link } from "react-router-dom";

import { Card } from "@/shared/ui/Card";
import { DataTable, type DataColumn } from "@/shared/ui/DataTable";
import { Chip } from "@/shared/ui/Chip";
import { useDocumentTitle } from "@/shared/hooks/useDocumentTitle";
import { isAuthed } from "@/shared/api/auth";
import { Wordmark } from "@/shared/ui/Wordmark";

import { COLLECTED, KIND_TONE, USE_END_NOTE } from "../lib/privacy_terms";

/** 수집 항목 표의 열. 긴 글은 셀 안에서 줄을 바꾼다(표 셀의 기본은 한 줄이다). */
const COLUMNS: DataColumn<(typeof COLLECTED)[number]>[] = [
  { label: "구분", sortable: false, cell: (r) => <Chip tone={KIND_TONE[r.kind] ?? "neutral"}>{r.kind}</Chip> },
  { label: "수집 항목", sortable: false, cell: (r) => <span className="block whitespace-normal text-label">{r.items}</span> },
  { label: "이용 목적", sortable: false, cell: (r) => <span className="block whitespace-normal">{r.purpose}</span> },
  // 읽는 사람이 가장 먼저 찾는 값이라 굵게 둔다.
  { label: "보유·이용 기간", sortable: false, cell: (r) => <span className="block whitespace-normal font-medium text-label">{r.period}</span> },
];

/**
 * 절 하나를 카드로 그린다. 번호는 제목 앞 회색 숫자다.
 * 절마다 면을 나눠 표와 본문이 한 덩어리로 읽히지 않게 한다.
 */
function Section({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <Card as="section" className="p-6">
      <h2 className="mb-3 flex items-baseline gap-2.5 text-headline text-label">
        <span className="tabular-nums text-label-3">{n}</span>
        {title}
      </h2>
      {children}
    </Card>
  );
}

/** 개인정보 수집·이용 동의 안내를 그린다. */
export function PrivacyPage() {
  // 로그인 없이 여는 화면이라 셸이 없다. 탭 제목을 직접 올린다.
  useDocumentTitle("개인정보 수집·이용 동의");
  return (
    <div className="min-h-screen bg-canvas px-4 py-12">
      <div className="mx-auto max-w-3xl">
        <header className="mb-8">
          {/* 인증 셸과 같은 워드마크다(호박색 R 표식, 제품 이름, 에디션 이름). */}
          <Wordmark />
          <h1 className="mt-5 text-title-1 text-label">개인정보 수집·이용 동의</h1>
          <p className="mt-2 text-callout text-label-2">
            이 서비스를 설치·운영하는 기관(이하 운영 기관)은 「개인정보 보호법」에 따라 아래와 같이
            개인정보를 수집·이용합니다.
          </p>
        </header>

        {/*
          수집 항목·이용 목적·보유 기간과 문장은 동의 문서라 화면 모양을 바꿀 때도 고치지 않는다.
          주의: 서버가 모으는 항목을 늘리면 이 목록도 같이 고친다(`COLLECTED` 주석).
        */}
        <div className="space-y-4 text-callout leading-[1.75] text-label-2">
          <Section n={1} title="수집 항목·이용 목적·보유 기간">
            {/*
              좁은 폭(640px 미만)에서는 행을 카드로 쌓는다. 375px에 네 열을 나란히 두면 한 열이
              60px 남짓이라 글자가 한 자씩 끊긴다.
            */}
            <div className="overflow-hidden rounded-card border border-separator">
              <DataTable
                bare
                stack
                pageSize={0}
                columns={COLUMNS}
                rows={COLLECTED}
                rowKey={(r) => r.kind}
              />
            </div>
            <p className="mt-3">{USE_END_NOTE}</p>
          </Section>

          <Section n={2} title="동의 거부 권리 및 불이익">
            {/*
              필수 동의는 이용의 조건이다. 서버가 동의 전 업무 요청을 막는다.
            */}
            <p>
              필수 항목 수집·이용 동의는 거부할 수 있습니다. 다만 동의하지 않으면 서비스를 이용할 수
              없습니다.
            </p>
          </Section>

          <Section n={3} title="제3자 제공 및 처리 위탁">
            <p>
              수집한 개인정보는 제3자에게 제공하지 않으며, 처리 위탁이 발생하는 경우 위탁받는 자와
              위탁 업무 내용을 본 페이지를 통해 사전 고지합니다.
            </p>
          </Section>

          <Section n={4} title="동의 기록 및 철회">
            {/*
              필수 동의만 따로 거두는 기능은 없다. 철회는 계정 삭제 요청으로 처리한다(`privacy_consent_withdrawal_unavailable`).
              선택 동의 기록이 남은 계정의 철회 안내는 내 계정 화면이 그 계정에게만 보여 준다.
            */}
            <p>
              동의 시각은 계정에 기록됩니다. 필수 항목 동의는 서비스 이용의 조건이므로
              동의 철회는 계정 삭제로 처리합니다. 계정 삭제는 최고관리자에게 요청해주세요.
            </p>
          </Section>
        </div>

        <footer className="mt-10 flex items-center justify-between gap-4 border-t border-separator pt-4 text-footnote text-label-3">
          {/*
            시행일은 설치마다 달라서 날짜를 적지 않는다.
            운영 기관이 수집 항목이나 보유 기간을 바꾸면 시행일을 개정일로 적고 개정 이력을 더한다.
          */}
          <div className="flex min-w-0 flex-col gap-0.5">
            <span>시행일: 서비스 운영 시작일</span>
          </div>
          {/*
            로그인 상태에 따라 돌아갈 링크를 고른다.
            `내 계정`에서 새 탭으로 연 사람에게 `로그인` 링크는 맞지 않는다.
            누르면 로그인 화면이 곧바로 홈으로 돌려보낸다.
          */}
          {isAuthed() ? (
            <Link to="/account" className="shrink-0 font-medium text-label underline underline-offset-4 hover:text-label-2">내 계정으로 돌아가기</Link>
          ) : (
            <Link to="/login" className="shrink-0 font-medium text-label underline underline-offset-4 hover:text-label-2">로그인으로 돌아가기</Link>
          )}
        </footer>
      </div>
    </div>
  );
}
