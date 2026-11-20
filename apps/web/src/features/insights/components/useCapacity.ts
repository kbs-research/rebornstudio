/**
 * `dashboard/capacity` 응답을 읽는 조회 훅.
 *
 * 개요의 `조치 필요`(`ActionNeeded`)가 장기 대기·납품 불가를 읽는다. 개요의 다른 카드도 같은 키로 읽으므로
 * 요청은 한 번만 간다. 여러 번 왕복하면 줄마다 다른 시점의 수가 섞인다.
 */
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { useProjects } from "@/features/projects";

import { fetchCapacity, insightsKeys } from "../api";

/** 주소의 프로젝트로 `capacity`를 조회한다. 프로젝트를 아직 받지 못했으면 조회하지 않는다. */
export function useCapacity() {
  const { slug } = useParams<{ slug: string }>();
  const projects = useProjects();
  const project = projects.data?.find((p) => p.slug === slug);
  return useQuery({
    queryKey: insightsKeys.capacity(project?.project_id),
    queryFn: () => fetchCapacity(project!.project_id),
    enabled: !!project,
  });
}
