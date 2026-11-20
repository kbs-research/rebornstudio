/**
 * 쓸 수 있는 저장소·분석 연결이 없거나 프로젝트가 저장소·분석 대상을 저장하지 않았을 때 프로젝트 화면에 띄우는 안내.
 *
 * `storage_ready`·`analysis_ready`가 거짓이면 고른 연결을 읽지 못했거나, 아직 고르지 않았는데 등록한
 * 연결이 하나도 없다. 어느 쪽이든 고치는 곳은 자격증명 연결이다.
 *
 * 판정은 서버가 프로젝트마다 준다(`storage_ready`·`analysis_ready`는 서버의 `presets.readiness`,
 * `analysis_stack_missing`은 `presets.target_missing`, `storage_stack_missing`은 `presets.storage_target_missing`).
 * 화면은 다시 판정하지 않는다. 같은 판정이 등록 버튼·분석 요청 버튼·서버의 409에 함께 쓰인다.
 * 결과(굵게)와 풀리는 방법(작게)을 두 줄로 쓴다.
 */
import { Link } from "react-router-dom";
import { TriangleAlert } from "lucide-react";

import { cn } from "@/shared/lib/cn";

/**
 * 안내 두 줄. 저장소가 없으면 등록·재생·납품이, 분석이 없으면 분석 요청만 막힌다.
 * 저장소·분석 대상을 저장하지 않은 프로젝트는 등록(과 분석 요청)이 막힌다. 그때 `settings`가 참이다.
 */
export function connectionNoticeText(
  storageReady: boolean,
  analysisReady: boolean,
  stackMissing = false,
  storageStackMissing = false,
): { title: string; body: string; settings?: boolean } | null {
  if (!storageReady && !analysisReady) {
    return {
      title: "저장소 연결과 분석 연결을 쓸 수 없어 영상 등록과 분석 요청이 막혀 있습니다",
      body: "최고관리자가 자격증명 연결에서 두 연결을 등록하거나 점검하면 풀립니다.",
    };
  }
  if (!storageReady) {
    return {
      title: "저장소 연결을 쓸 수 없어 영상 등록·재생·납품이 막혀 있습니다",
      body: "최고관리자가 자격증명 연결에서 NCP 저장소 연결을 등록하거나 점검하면 풀립니다.",
    };
  }
  if (storageStackMissing && stackMissing) {
    return {
      title: "저장소·분석 대상을 저장하지 않아 영상 등록과 분석 요청이 막혀 있습니다",
      body: "프로젝트 관리자가 설정의 분석 연동에서 두 대상을 저장하면 풀립니다.",
      settings: true,
    };
  }
  if (storageStackMissing) {
    return {
      title: "저장소 대상을 저장하지 않아 영상 등록이 막혀 있습니다",
      body: "프로젝트 관리자가 설정의 분석 연동에서 저장소 대상을 저장하면 풀립니다.",
      settings: true,
    };
  }
  if (stackMissing) {
    return {
      title: "분석 대상을 저장하지 않아 영상 등록과 분석 요청이 막혀 있습니다",
      body: "프로젝트 관리자가 설정의 분석 연동에서 분석 대상을 저장하면 풀립니다.",
      settings: true,
    };
  }
  if (!analysisReady) {
    return {
      title: "분석 연결을 쓸 수 없어 분석 요청이 막혀 있습니다",
      body: "영상 등록은 됩니다. 최고관리자가 자격증명 연결에서 분석 연결을 등록하거나 점검하면 풀립니다.",
    };
  }
  return null;
}

/**
 * 연동이 없는 프로젝트에 안내를 그린다. 막힌 것이 없으면 아무것도 그리지 않는다.
 * 프로젝트를 아직 받지 못했으면(`undefined`) 그리지 않는다. 그렸다 지우면 화면이 흔들린다.
 * 저장소·분석 대상 안내에는 설정의 분석 연동으로 가는 링크를 붙인다.
 * 저장소·분석 대상 카드가 같은 페이지에 있으면(`stackHandled`) 그 안내를 띄우지 않는다. 카드가 같은 사실을 보여 준다.
 */
export function ConnectionNotice({
  project,
  className,
  stackHandled = false,
}: {
  project: {
    slug?: string; storage_ready?: boolean; analysis_ready?: boolean;
    analysis_stack_missing?: boolean; storage_stack_missing?: boolean;
  } | undefined;
  className?: string;
  stackHandled?: boolean;
}) {
  if (!project) return null;
  const text = connectionNoticeText(
    project.storage_ready ?? true, project.analysis_ready ?? true,
    !stackHandled && (project.analysis_stack_missing ?? false),
    !stackHandled && (project.storage_stack_missing ?? false),
  );
  if (!text) return null;
  return (
    <div role="status" className={cn("flex gap-2.5 rounded-card bg-bad-muted px-3.5 py-3", className)}>
      <TriangleAlert size={16} className="mt-0.5 shrink-0 text-bad-text" aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-footnote font-semibold text-bad-text">{text.title}</p>
        <p className="mt-0.5 text-caption text-label-2">{text.body}</p>
        {text.settings && project.slug && (
          <Link
            to={`/p/${project.slug}/settings?tab=link`}
            className="mt-1 inline-block text-caption font-semibold text-accent underline-offset-2 hover:underline"
          >
            분석 연동 설정 열기
          </Link>
        )}
      </div>
    </div>
  );
}
