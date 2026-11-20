/**
 * 디자인 시스템 화면(`/design-system`). 디자인 관례를 보여 주는 기준 화면이다.
 *
 * 토큰(표면, 라벨, 액센트, 상태, 타입, radius)과 관례(점선은 정의,
 * 층, 포커스)를 라이트/다크 토글로 확인한다.
 * 인증 없이 열리는 최상위 라우트이고, 개발 빌드에서만 등록된다(`router.tsx`).
 *
 * 이 화면에는 실제 컴포넌트를 둔다.
 * 갤러리는 사람들이 복사해 가는 곳이라 직접 흉내 낸 샘플을 두면 틀린 모양이 퍼진다.
 *
 * 값을 바꾸면 이 화면도 함께 확인한다.
 */
import { useState } from "react";
import { Moon, Sun, Trash2 } from "lucide-react";

import { Chip } from "@/shared/ui/Chip";
import { EmptyState } from "@/shared/ui/EmptyState";
import { Modal } from "@/shared/ui/Modal";
import { StatLine } from "@/shared/ui/StatLine";
import { DataTable } from "@/shared/ui/DataTable";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/shared/ui/Toast";
import { Button } from "@/shared/ui/Button";
import { Input } from "@/shared/ui/Input";
import { cn } from "@/shared/lib/cn";
import { DefinedTerm } from "@/shared/ui/Tooltip";
import { useTheme } from "@/store/theme";
import { useDocumentTitle } from "@/shared/hooks/useDocumentTitle";

/** 제목과 그리드로 된 섹션 래퍼를 그린다. */
function Section({ title, children, cols = "grid-cols-2 sm:grid-cols-4" }: { title: string; children: React.ReactNode; cols?: string }) {
  return (
    <section className="mb-10">
      <h2 className="text-title-2 mb-3 text-label">{title}</h2>
      <div className={`grid gap-4 ${cols}`}>{children}</div>
    </section>
  );
}

/** 색 견본을 그린다. 표면 위에 상자와 라벨을 둔다. */
function Swatch({ name, className }: { name: string; className: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className={`h-16 rounded-card border border-separator ${className}`} />
      <div className="text-footnote text-label-3">{name}</div>
    </div>
  );
}

/** 갤러리 표의 표본 데이터. 실제 데이터를 흉내 낸 세 줄이고, 자릿수가 다른 수를 일부러 섞었다. */
const DS_TABLE_ROWS = [
  { job: "2026-08-14 수집분", videos: 1284, len: "12:04:37", id: "a1b2c3d4", status: "완료", tone: "ok" as const },
  // 표본 이름은 특정 기관이 아닌 일반 이름으로 쓴다. 이 화면은 어느 설치 환경에서나 같은 내용으로 보인다.
  { job: "뉴스 8월 2주", videos: 97, len: "3:41:02", id: "9f0e1d2c", status: "분석 중", tone: "note" as const },
  { job: "스포츠 하이라이트", videos: 8, len: "0:22:15", id: "5a6b7c8d", status: "실패", tone: "bad" as const },
];

/** 디자인 시스템 갤러리를 그린다. */
export function DesignSystemPage() {
  const { toast } = useToast();
  const { theme, toggle } = useTheme();
  const [confirmOpen, setConfirmOpen] = useState(false);
  // 셸 밖 화면이라 탭 제목을 직접 올린다.
  useDocumentTitle("디자인 시스템");

  return (
    <div className="min-h-screen bg-canvas px-8 py-8 text-label">
      <header className="mb-10 flex items-center justify-between">
        <div>
          <h1 className="text-display text-label">디자인 시스템</h1>
          <p className="text-callout mt-1 text-label-2">
            잉크 한 색의 농도 사다리 · 층은 이름으로 · 물리 어휘 하나 · 토큰 기준 하나
          </p>
        </div>
        {/*
          버튼에는 누른 뒤의 테마를 표시한다. 지금 테마를 적으면 누른 뒤의 상태인지 알 수 없다.
          글자 기호(☀ ☾)는 글꼴마다 모양이 달라 아이콘을 쓴다.
        */}
        <Button variant="secondary" onClick={toggle}>
          {theme === "light" ? <Moon size={14} aria-hidden="true" /> : <Sun size={14} aria-hidden="true" />}
          {theme === "light" ? "다크 테마로 보기" : "라이트 테마로 보기"}
        </Button>
      </header>

      <Section title="배경 · 표면 (elevation)">
        <Swatch name="bg-canvas" className="bg-canvas" />
        <Swatch name="bg-canvas-2" className="bg-canvas-2" />
        <Swatch name="bg-panel" className="bg-panel" />
        <Swatch name="bg-raised" className="bg-raised" />
      </Section>

      <Section title="라벨 (전경 4계층)" cols="grid-cols-1">
        <div className="rounded-card border border-separator bg-panel p-5">
          <p className="text-body text-label">label · 본문/제목 (primary)</p>
          <p className="text-body text-label-2">label-2 · 보조 텍스트 (secondary)</p>
          <p className="text-body text-label-3">label-3 · 메타/muted (tertiary)</p>
          <p className="text-body text-label-4">label-4 · 극희미 (quaternary)</p>
          <p className="text-body text-placeholder">placeholder · 입력 자리표시</p>
        </div>
      </Section>

      <Section title="액센트 · fill">
        <Swatch name="bg-accent" className="bg-accent" />
        <Swatch name="bg-accent-muted" className="bg-accent-muted" />
        <Swatch name="bg-fill" className="bg-fill" />
        <Swatch name="bg-fill-2" className="bg-fill-2" />
      </Section>

      <Section title="상태 (solid · muted+text)">
        {([
          ["ok", "정상", "bg-ok", "bg-ok-muted text-ok-text"],
          ["warn", "주의", "bg-warn", "bg-warn-muted text-warn-text"],
          ["bad", "위험", "bg-bad", "bg-bad-muted text-bad-text"],
          ["note", "정보", "bg-note", "bg-note-muted text-note-text"],
        ] as const).map(([key, label, solid, muted]) => (
          <div key={key} className="flex flex-col gap-1.5">
            <div className={`h-16 rounded-card ${solid}`} />
            <span className={`inline-flex w-fit items-center rounded-capsule px-2.5 py-0.5 text-caption font-medium ${muted}`}>
              {label}
            </span>
          </div>
        ))}
      </Section>

      <section className="mb-10">
        <h2 className="text-title-2 mb-3 text-label">타입 스케일 (한국어)</h2>
        <div className="space-y-2 rounded-card border border-separator bg-panel p-5">
          <p className="text-display text-label">Display 32 · 보도영상 가공</p>
          <p className="text-title-1 text-label">Title 1 · 24 · 작업자 운영</p>
          <p className="text-title-2 text-label">Title 2 · 20 · 분석 현황</p>
          <p className="text-headline text-label">Headline · 16 · 카드 제목</p>
          <p className="text-body text-label">Body · 15 · 본문 기본 (한국어 행간 1.6)</p>
          <p className="text-callout text-label-2">Callout · 14 · 테이블 셀·보조</p>
          <p className="text-footnote text-label-3">Footnote · 13 · 메타데이터 / 캡션</p>
          <p className="text-caption text-label-3">Caption · 12 · 배지 / 스케일의 바닥 (밀집 화면인 워크벤치만 예외로 10~11px)</p>
        </div>
      </section>

      {/*
        라운드 기준은 이 시맨틱 이름 4종이다.
        숫자 이름(`rounded-md` 등)은 이 저장소에 정의가 없어 Tailwind 기본값이 적용된다.
      */}
      <Section title="Radius 기준 (이름으로 고른다)">
        {[
          ["rounded-field", "rounded-field 7 · 입력"],
          ["rounded-control", "rounded-control 7 · 버튼·메뉴 항목"],
          ["rounded-card", "rounded-card 10 · 카드·패널"],
          ["rounded-capsule w-16", "rounded-capsule · 배지·칩"],
        ].map(([cls, label]) => (
          <div key={label} className="flex flex-col gap-1.5">
            <div className={`h-16 bg-fill ${cls}`} />
            <span className="text-footnote text-label-3">{label}</span>
          </div>
        ))}
      </Section>

      {/*
        주의: 실제 컴포넌트를 쓴다. 갤러리의 모양이 틀리면 복사해 간 화면에 틀린 모양이 퍼진다.
        실제 `Button`에는 그라디언트, 광택, 접촉 그림자가 있고, 직접 만든 `<button className="bg-accent">`는 평면이다.
      */}
      <Section title="컴포넌트 샘플 (실제 컴포넌트)" cols="grid-cols-1">
        <div className="flex flex-wrap items-center gap-3 rounded-card border border-separator bg-panel p-5">
          <Button>Primary 액션</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="danger">Danger</Button>
          <Button variant="ghost">Ghost</Button>
          <Input placeholder="검색…" aria-label="검색" className="w-40" />
          {/*
            배지 문구는 지금 제품이 쓰는 말로 쓴다.
          */}
          <Chip tone="ok">승인</Chip>
          <Chip tone="bad">반려</Chip>
        </div>

        {/*
          칩 톤 전체. 색이 곧 의미이므로 이 목록에 없는 색을 직접 그리면 사용자가 배운 뜻과 어긋난다.
          상태 enum은 `StatusBadge`로 그린다. `StatusBadge`는 `Chip` 위에 만들어져 크기와 여백 기준을 공유한다.
        */}
        <div className="flex flex-wrap items-center gap-2 rounded-card border border-separator bg-panel p-5">
          <Chip>중립 라벨</Chip>
          <Chip tone="quiet">약한 라벨</Chip>
          <Chip tone="ok">성공</Chip>
          <Chip tone="warn">주의</Chip>
          <Chip tone="bad">위험</Chip>
          <Chip tone="note">정보</Chip>
          <Chip tone="accent">강조</Chip>
          <Chip outlined>테두리</Chip>
          <StatusBadge status="analyzing" />
          <StatusBadge status="approved" />
        </div>

        {/*
          토스트는 굵은 줄에 결과를, 작은 줄에 다음 할 일을 쓴다.
          종류는 아이콘과 색으로 표시하므로 글자로 다시 쓰지 않는다.
          버튼을 눌러 실제 모양을 확인한다. 갤러리에 예시가 없으면 직접 만든 토스트가 다시 늘어난다.
        */}
        <div className="flex flex-wrap items-center gap-2 rounded-card border border-separator bg-panel p-5">
          <Button size="sm" variant="secondary"
            onClick={() => toast("success", "3건 제외를 취소했습니다", "목록에서 다시 확인할 수 있습니다.")}>
            결과 + 안내
          </Button>
          <Button size="sm" variant="secondary"
            onClick={() => toast("warning", "지금 받을 수 있는 작업이 없습니다", "잠시 후 다시 눌러주세요.")}>
            주의
          </Button>
          {/* 실패 토스트의 굵은 줄은 `저장 실패`처럼 명사구다(`failToast` 관례). */}
          <Button size="sm" variant="secondary" onClick={() => toast("error", "저장 실패")}>
            결과만
          </Button>
        </div>
      </Section>

      {/*
        표는 앱에서 가장 많이 쓰는 화면 요소라 여러 화면이 같은 `DataTable`을 쓴다.
        그래서 갤러리에도 실제 `DataTable`을 둔다. 직접 흉내 낸 표를 두면 흉내 낸 모양이 퍼진다.
        관례가 갤러리에 없으면 호출부가 셀 안에 크기와 글자색을 직접 적게 된다.
      */}
      <Section title="표: 수와 잉크" cols="grid-cols-1">
        <div className="rounded-card border border-separator bg-panel p-5">
          <p className="mb-3 text-footnote leading-relaxed text-label-3">
            <span className="font-medium text-label-2">열이 형식을 정하고 셀은 값만 냅니다.</span>{" "}
            <code className="rounded-control bg-fill px-1 text-caption">kind</code>가{" "}
            <code className="rounded-control bg-fill px-1 text-caption">number</code>·
            <code className="rounded-control bg-fill px-1 text-caption">date</code>면 표가
            자릿수를 맞추고(<code className="rounded-control bg-fill px-1 text-caption">tabular-nums</code>),
            크기·잉크는{" "}
            <code className="rounded-control bg-fill px-1 text-caption">tone</code>이 정합니다.
            셀 안에 <code className="rounded-control bg-fill px-1 text-caption">text-caption text-label-3</code>{" "}
            같은 짝을 직접 적지 않습니다.
          </p>
          <DataTable
            bare
            rows={DS_TABLE_ROWS}
            rowKey={(r) => r.job}
            columns={[
              { label: "등록 묶음", kind: "title", value: (r) => r.job },
              { label: "영상", kind: "number", align: "right", value: (r) => r.videos, cell: (r) => `${r.videos.toLocaleString("ko-KR")}건` },
              { label: "총 길이", kind: "date", align: "right", tone: "muted", value: (r) => r.len },
              { label: "작업 ID", kind: "title", mono: true, tone: "muted", value: (r) => r.id },
              { label: "상태", kind: "status", sortable: false, cell: (r) => <Chip tone={r.tone}>{r.status}</Chip> },
            ]}
          />
          <dl className="mt-4 grid gap-1.5 text-caption text-label-3 sm:grid-cols-3">
            <div><dt className="inline font-medium text-label-2">기본</dt> · 이 표가 보여 주려는 값</div>
            <div><dt className="inline font-medium text-label-2">tone=&quot;muted&quot;</dt> · 곁들이는 정보(시각·경과)</div>
            <div><dt className="inline font-medium text-label-2">tone=&quot;faint&quot;</dt> · 없음·보조 표시</div>
          </dl>
          <p className="mt-3 text-caption leading-relaxed text-label-3">
            <span className="font-medium text-label-3">mono는 ID·해시에만.</span> 수에 주면 글꼴이
            바뀌어 한 표 안에 서체가 둘이 됩니다. 자릿수를 맞추는 일은 kind가 합니다.{" "}
            <span className="font-medium text-label-3">빽빽함은 padding으로.</span> 본문 크기는
            언제나 text-callout이고 density는 여백만 바꿉니다. 글자는 줄이지 않습니다.
          </p>
        </div>
      </Section>

      {/* ── 관례: 이 앱의 시각 규칙. 사용자가 배운 의미와 화면을 맞추는 규칙이다. ── */}
      <Section title="관례" cols="grid-cols-1">
        <div className="space-y-4 rounded-card border border-separator bg-panel p-5">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-callout text-label-2">
            <DefinedTerm define="무엇을 세는지, 계산 대상이 무엇인지, 어느 쪽이 좋은지를 여기 적습니다. 점선인데 설명이 없으면 관례와 어긋납니다.">
              점선 밑줄
            </DefinedTerm>
            <span className="text-label-3">= 호버·포커스하면 정의가 나옵니다. 실선(링크=이동)과 구분됩니다.</span>
          </div>
          <div className="flex flex-wrap items-center gap-4">
            <div className="rounded-card border border-separator bg-panel px-3 py-2 shadow-pop text-footnote text-label-2">
              앵커드 팝오버 · shadow-pop
            </div>
            <span className="text-caption text-label-3">
              트리거에 붙어 잠깐 뜨는 판은 전부 이 그림자를 씁니다. 모달·시트·토스트는 다른 부류입니다.
            </span>
          </div>

          {/*
            층(그림자)은 진하기 대신 요소의 종류로 고른다.
            값은 tokens.css에 있고, 이 섹션은 쓰임새를 보여 준다.
            쓰임새를 모르면 같은 카드가 화면마다 다른 깊이로 그려진다.
          */}
          <div className="flex flex-wrap items-end gap-3 pt-1">
            {[
              ["없음", "바닥에 놓인 것 · 표 행·사이드바", ""],
              ["shadow-xs", "살짝 뜨는 것 · 세그먼트 알약", "shadow-xs"],
              ["shadow-e1", "페이지 위의 카드", "shadow-e1"],
              ["shadow-pop", "트리거에 붙은 패널", "shadow-pop"],
              ["shadow-e3", "모든 것 위의 층 · 모달·토스트", "shadow-e3"],
            ].map(([name, use, cls]) => (
              <div key={name} className="w-[9.5rem]">
                <div className={cn("h-12 rounded-card border border-separator bg-panel", cls)} />
                <div className="mt-1.5 font-mono text-caption text-label-2">{name}</div>
                <div className="text-caption text-label-3">{use}</div>
              </div>
            ))}
          </div>

          {/*
            포커스 스타일은 값보다 어느 CSS 레이어에 있는지가 중요하다.
            포커스 표시는 키보드 포커스에만 보이므로 샘플은 Tab 키로 확인한다.
          */}
          <div className="flex flex-wrap items-center gap-3 pt-1 text-caption text-label-3">
            <button
              type="button"
              className="rounded-capsule border border-separator-strong px-3 py-1 text-footnote text-label-2"
            >
              Tab으로 눌러 보세요 (기본 아웃라인)
            </button>
            <button
              type="button"
              className="rounded-capsule bg-fill px-3 py-1 text-footnote text-label-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
            >
              링으로 덮은 것
            </button>
            <span>
              전역 아웃라인은 <span className="font-mono">@layer base</span>의 <b>기본값</b>이라
              컴포넌트가 <span className="font-mono">outline-none</span>으로 덮을 수 있습니다.
              레이어 밖에 두면 못 덮어 <b>두 겹</b>이 됩니다.
            </span>
          </div>
        </div>
      </Section>

      {/* ── 슬롯: 부품에 슬롯이 없으면 호출부가 직접 만든다 ── */}
      <Section title="슬롯" cols="grid-cols-1">
        <div className="space-y-6 rounded-card border border-separator bg-panel p-5">
          {/*
            지표 줄.
            두 배치를 나란히 두어 언제 어느 쪽을 쓰는지 모양으로 비교하게 한다.
          */}
          <div className="space-y-3">
            <h3 className="text-callout font-semibold text-label-2">StatLine 의 두 배치 (inline · stacked)</h3>
            <div className="rounded-card border border-separator bg-canvas px-4 py-3">
              <StatLine
                items={[
                  { label: "전체 프로젝트", value: 7 },
                  { label: "전체 작업", value: "329" },
                  { label: "전체 진척", value: "35%", hint: "114 / 329" },
                ]}
              />
            </div>
            <div className="rounded-card border border-separator bg-canvas px-4 py-3">
              <StatLine
                layout="stacked"
                items={[
                  { label: "남은 비용", value: "약 ₩171,533", hint: "17.15시간 남음" },
                  { label: "누적 비용", value: "₩129,284", hint: "12.93시간 처리" },
                  { label: "시간당 단가", value: "₩10,000" },
                ]}
              />
            </div>
            <p className="text-caption leading-relaxed text-label-3">
              <span className="font-medium text-label-3">값이 셋 이상이거나 단위가 붙으면 stacked.</span>{" "}
              인라인은 이름과 단서가 값 옆에 이어 붙어, 꼬리가 길어지면 묶음 경계가 안 보입니다.
              2단은 이름을 제 값 바로 아래 두어 간격을 넓히지 않고 가릅니다.
            </p>
          </div>

          {/* 빈 상태. 같은 부품의 두 크기를 한 화면에 둔다. */}
          <div className="space-y-3">
            <h3 className="text-callout font-semibold text-label-2">EmptyState 의 두 크기 (기본 · dense)</h3>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="rounded-card border border-separator bg-canvas">
                <EmptyState message="작업 없음" description="받은 작업이 여기에 쌓입니다." />
              </div>
              <div className="rounded-card border border-separator bg-canvas">
                <EmptyState dense message="작업 없음" description="받은 작업이 여기에 쌓입니다." />
              </div>
            </div>
            <p className="text-caption leading-relaxed text-label-3">
              <span className="font-medium text-label-2">페이지가 비면 기본, 카드 안이면 dense.</span>{" "}
              머리글은 이름이라 명사구로 쓰고, 안내만 문장으로 씁니다.
            </p>
          </div>

          {/* 대화상자. 버튼을 눌러야 보이므로 갤러리에 실제 창을 둔다. */}
          <div className="space-y-3">
            <h3 className="text-callout font-semibold text-label-2">Modal 의 새 슬롯 (tone · footer)</h3>
            <Button variant="secondary" size="sm" onClick={() => setConfirmOpen(true)}>
              파괴적 확인창 열기
            </Button>
            <p className="text-caption leading-relaxed text-label-3">
              <span className="font-medium text-label-3">액션 줄은 창이 그립니다.</span>{" "}
              호출부는 버튼만 넘기고 여백·정렬·구분선은 부품이 갖습니다. 되돌릴 수 없는 창은
              tone으로 제목 줄에서 색을 얻습니다.
            </p>
          </div>
        </div>
      </Section>

      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="표본 삭제"
        icon={<Trash2 size={16} />}
        tone="danger"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setConfirmOpen(false)}>
              취소
            </Button>
            <Button variant="danger" size="sm" onClick={() => setConfirmOpen(false)}>
              삭제
            </Button>
          </>
        }
      >
        <p>
          이 창은 갤러리 표본이라 아무것도 지우지 않습니다. 실제 창에서는 무엇이 지워지는지를
          여기 적어, 누르기 전에 판단이 되게 합니다.
        </p>
      </Modal>
    </div>
  );
}
