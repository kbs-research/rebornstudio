/**
 * 프로젝트의 분석 스택 정보를 돌려준다. 스택 라벨·구성·벤더 사용 여부의 단일 출처다.
 *
 * 프로젝트를 만들 때 고르는 분석 스택(`analysis_preset`)은 스토리지·분석기·씬 분할을
 * 한꺼번에 정하고, 생성 뒤에는 바뀌지 않는다.
 * 이 훅이 있어야 프로젝트를 만든 뒤에도 MI를 쓰는지 샷 검출을 쓰는지 알 수 있다.
 *
 * 벤더 관련 카드를 보일지도 이 훅이 정한다.
 * `ncp-shots`처럼 벤더 분석기가 없는 스택에 벤더 비용·처리량 카드(단가만 있고 실적은 0)를 띄우면
 * 쓰지 않는 기능을 쓰는 것처럼 보인다.
 * 화면마다 따로 판단하면 결과가 달라지므로 `usesVendorAnalyzer` 한 곳에서 판단한다.
 * 판단 근거는 프리셋 키 하드코딩 대신 서버가 주는 `adapter_key`라서 새 스택이 늘어도 화면을 고치지 않는다.
 */
import { useQuery } from "@tanstack/react-query";

import type { ProjectDTO } from "@/shared/api/client";
import { fetchAnalysisPresets, projectKeys } from "@/features/projects";

/** 이 저장소의 코드로 도는 어댑터 키. 이 집합에 없는 키는 벤더 분석기로 본다. */
const OWN_ADAPTERS = new Set(["scenedetect", "dummy", ""]);

export interface AnalysisStack {
  /** 프리셋 키(`ncp-mi`·`ncp-shots`). */
  key: string;
  /** 사람이 읽는 이름. 서버가 준 값을 그대로 쓰고 화면에서 다시 만들지 않는다. */
  label: string;
  /** 프리셋을 고를 때 보이는 설명. 카탈로그에서 온다. */
  description?: string;
  /** 원본이 저장되는 곳(`ncp`·`aws`). */
  storageKey?: string;
  /** 캡션 초안을 만드는 어댑터. */
  adapterKey?: string;
  /** 씬을 나누는 어댑터. `null`이면 분석기가 씬도 함께 나눈다. */
  sceneAdapterKey?: string | null;
  /** 벤더 분석기를 쓰면 true. 비용·호출 카드를 보일지 정하는 근거다. */
  usesVendorAnalyzer: boolean;
}

/**
 * 프로젝트의 분석 스택을 카탈로그 정보와 합쳐 돌려준다.
 *
 * 카탈로그(`/projects/analysis-presets`)는 프로젝트마다 다르지 않다.
 * 그래서 쿼리 키에 프로젝트를 넣지 않고 생성 화면과 캐시를 공유한다(추가 요청 0건).
 *
 * @param project 화면이 이미 가진 프로젝트. 없으면 `null`을 돌려준다.
 */
export function useAnalysisStack(project: ProjectDTO | undefined): AnalysisStack | null {
  const presets = useQuery({
    queryKey: projectKeys.analysisPresets(),
    queryFn: fetchAnalysisPresets,
    staleTime: 60 * 60 * 1000, // 카탈로그는 서버 코드에 정의되어 있어 자주 바뀌지 않는다.
  });

  if (!project) return null;
  const p = presets.data?.find((x) => x.key === project.analysis_preset);
  return {
    key: project.analysis_preset,
    // 라벨은 프로젝트 DTO의 값을 쓴다. 그래서 카탈로그에서 사라진 이전 프리셋이어도
    // 프로젝트를 만든 스택의 이름이 보인다.
    label: project.analysis_preset_label,
    description: p?.description,
    storageKey: p?.storage_key,
    adapterKey: p?.adapter_key,
    sceneAdapterKey: p?.scene_adapter_key,
    // 카탈로그를 아직 받지 못했으면 벤더를 쓰지 않는다고 본다.
    // 그러면 카드가 잠깐 떴다 사라지는 대신 조금 늦게 나타난다.
    usesVendorAnalyzer: p != null && !OWN_ADAPTERS.has(p.adapter_key ?? ""),
  };
}
