/**
 * 분석 스택 카드(설정 `기본 정보`). 이 프로젝트가 어떤 스택으로 만들어졌는지 보여 준다.
 *
 * 프로젝트를 만들 때 고른 스택은 스토리지·분석기·장면 분할을 한꺼번에 정하고,
 * 생성 뒤에는 바뀌지 않는다.
 * 되돌릴 수 없는 선택일수록 그 근거가 같은 화면에 있어야 한다.
 * 이 카드가 없으면 만든 뒤에는 이 프로젝트가 MI를 쓰는지 샷 검출을 쓰는지 화면 어디에도 나오지 않는다.
 *
 * 바꿀 수 없다는 사실도 함께 쓴다. 쓰지 않으면 관리자가 편집 필드를 찾다가 없어서 고장으로 읽는다.
 * 카드 하나에 머리와 세 줄을 둔다.
 */
import { Lock } from "lucide-react";

import { Card } from "@/shared/ui/Card";

import type { AnalysisStack } from "@/hooks/useAnalysisStack";

/** 스토리지 키를 화면 이름으로 바꾼다. 사전에 없는 키는 이름을 지어내지 않고 그대로 보여 준다. */
const STORAGE_KO: Record<string, string> = { ncp: "네이버 클라우드", aws: "AWS S3" };

/**
 * 어댑터 키를 화면 이름으로 바꾼다.
 *
 * 이름 옆에 괄호 설명(`샷 검출 (자체)`)을 붙이지 않는다.
 * 괄호가 설명을 대신하면 이름만으로 뜻이 전달되지 않는다.
 * 필요한 사실은 이름 안에 넣는다.
 */
const ADAPTER_KO: Record<string, string> = {
  scenedetect: "샷 검출",
  bedrock: "AWS Bedrock",
  dummy: "검증용 더미",
};

/**
 * 어댑터 키의 화면 이름을 돌려준다. 키가 없으면 `—`를 돌려준다.
 * 사전에 없는 키는 `maiu`만 이름으로 바꾸고 나머지는 그대로 보여 준다.
 */
function adapterName(key?: string): string {
  if (!key) return "—";
  return ADAPTER_KO[key] ?? (key === "maiu" ? "Media Intelligence" : key);
}

/** 분석 스택 카드를 그린다. 스택 정보가 아직 없으면 아무것도 그리지 않는다. */
export function AnalysisStackCard({ stack }: { stack: AnalysisStack | null }) {
  if (!stack) return null;

  const rows: { label: string; value: string; hint?: string }[] = [
    {
      label: "원본 저장소",
      value: stack.storageKey ? (STORAGE_KO[stack.storageKey] ?? stack.storageKey) : "—",
    },
    {
      /*
        벤더 분석기가 없는 스택에는 캡션 초안이 없다. 없으면 없다고 쓰고 누가 캡션을 쓰는지 알린다.
        캡션을 쓰는 사람은 역할 이름으로 부른다(라벨러, 용어 표준).
        영상을 나누는 어댑터(`샷 검출`)를 이 줄에 적으면 샷 검출이 초안을 만드는 것처럼 읽힌다.
      */
      label: "캡션 초안",
      // 카탈로그를 아직 받지 못했으면(`adapterKey` 없음) 값을 알 수 없다. `없음`으로 단정하지 않는다.
      value: !stack.adapterKey ? "—" : stack.usesVendorAnalyzer ? adapterName(stack.adapterKey) : "없음",
      hint: !stack.adapterKey ? undefined : stack.usesVendorAnalyzer ? "분석 비용이 듭니다" : "라벨러가 처음부터 씁니다",
    },
    {
      label: "영상 나누기",
      value: stack.sceneAdapterKey
        ? adapterName(stack.sceneAdapterKey)
        : adapterName(stack.adapterKey),
      // `scene_adapter_key`가 없으면 분석기가 영상을 함께 나눈다는 뜻이다.
      // 비워 두면 "나누지 않는다"로 읽히므로 무엇이 나누는지 쓴다.
      hint: stack.sceneAdapterKey ? "샷이 바뀌는 지점으로" : "분석하면서 의미 단위로",
    },
  ];

  /*
    카드 머리에 스택 이름과 "만든 뒤 바꿀 수 없음" 표시를 둔다.
    바꿀 수 없는 이유를 문장으로 풀지 않는다. 입력란이 없는 까닭은 이 표시 하나로 읽힌다.
    스택 설명(`description`)은 아래 세 줄과 같은 사실을 되풀이해 싣지 않는다.
  */
  return (
    <Card as="section" aria-label="분석 방식">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1 border-b border-separator px-5 py-4">
        <div className="min-w-0">
          <h3 className="text-callout font-semibold text-label">분석 방식</h3>
          <p className="mt-0.5 text-footnote text-label-2">{stack.label}</p>
        </div>
        <span className="inline-flex shrink-0 items-center gap-1.5 text-footnote text-label-2">
          <Lock className="size-3.5" aria-hidden="true" />
          만든 뒤 바꿀 수 없음
        </span>
      </div>
      <dl className="divide-y divide-separator">
        {rows.map((r) => (
          <div key={r.label} className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5 px-5 py-3">
            <dt className="w-28 shrink-0 text-footnote text-label-2">{r.label}</dt>
            <dd className="text-footnote font-semibold text-label">{r.value}</dd>
            {r.hint && <dd className="ml-auto text-caption text-label-2">{r.hint}</dd>}
          </div>
        ))}
      </dl>
    </Card>
  );
}
