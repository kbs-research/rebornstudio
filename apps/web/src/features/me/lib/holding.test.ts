/**
 * 보유를 바꾸는 뮤테이션이 `내 작업`의 보유 수까지 무효화하는지 검사한다.
 *
 * 판정 뒤 목록 키만 무효화하고 보유 수가 실린 대기 수 키를 그대로 두면, 검수자가 마지막 건을 승인하고
 * `내 작업`으로 돌아왔을 때 목록은 비었는데 머리는 "보유 중 1건"으로 남는다.
 * `tsc`도 빌드도 이 누락을 잡아내지 못해 소스를 읽어 검사한다.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import type { QueryKey } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { meKeys } from "../api";
import { invalidateHolding } from "./holding";

const SRC = resolve(fileURLToPath(new URL(".", import.meta.url)), "../../..");

/** 보유를 바꾸는 서버 호출. 이 호출을 하는 뮤테이션은 보유 수를 무효화해야 한다. */
const HOLDING_CALLS = [
  "nextWork", "submitWork", "nextReview", "decideReview", "reportDeidentMiss",
];

/** 테스트를 뺀 `src` 아래 TypeScript 파일 경로 목록을 구한다. */
function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...sourceFiles(p));
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p);
  }
  return out;
}

/**
 * 보유를 바꾸는 호출을 하는데 성공 처리에서 보유 수를 무효화하지 않는 `useMutation`을 `파일:줄: 호출`로 구한다.
 * 뮤테이션 범위는 그 `useMutation`부터 다음 `useMutation` 앞까지다.
 */
function missingHoldingRefresh(path: string, text: string): string[] {
  const starts = [...text.matchAll(/useMutation\b/g)].map((m) => m.index ?? 0);
  const hits: string[] = [];
  starts.forEach((start, i) => {
    const block = text.slice(start, starts[i + 1] ?? text.length);
    const call = HOLDING_CALLS.find((c) => new RegExp(`mutationFn[^]*?\\b${c}\\(`).test(block.slice(0, 600)));
    if (!call) return;
    // 성공 처리만 본다. 실패 처리의 무효화는 성공한 보유 변경을 반영하지 않는다.
    const onSuccess = block.match(/onSuccess[^]*?(?=\bon(?:Error|Settled|Mutate)\b|$)/)?.[0] ?? "";
    if (/invalidateHolding\(|WaitingAll\(\)/.test(onSuccess)) return;
    hits.push(`${path}:${text.slice(0, start).split("\n").length}: ${call}`);
  });
  return hits;
}

/** 무효화한 키 앞부분 중 하나가 `key`의 앞부분이면 참. react-query의 앞부분 일치와 같다. */
function covered(key: QueryKey, prefixes: QueryKey[]): boolean {
  return prefixes.some((p) => p.every((v, i) => key[i] === v));
}

describe("보유를 바꾸면 보유 수도 다시 받는다", () => {
  it("검수·라벨링 묶음은 내 작업이 읽는 보유 목록과 보유 수 키를 덮는다", () => {
    const seen: QueryKey[] = [];
    const qc = { invalidateQueries: (f: { queryKey: QueryKey }) => { seen.push(f.queryKey); return Promise.resolve(); } };
    invalidateHolding(qc, "review");
    expect(covered(meKeys.reviewWaiting("p"), seen)).toBe(true);
    expect(covered(meKeys.heldReviews("p"), seen)).toBe(true);
    seen.length = 0;
    invalidateHolding(qc, "work");
    expect(covered(meKeys.workWaiting("p"), seen)).toBe(true);
    expect(covered(meKeys.heldWork("p"), seen)).toBe(true);
  });

  it("보유 수를 무효화하지 않는 뮤테이션을 찾아낸다", () => {
    const text = [
      "const decide = useMutation({",
      "  mutationFn: (b) => decideReview(id, b),",
      "  onSuccess: () => { void qc.invalidateQueries({ queryKey: pipelineKeys.reviewAll() }); },",
      "  onError: () => { void qc.invalidateQueries({ queryKey: meKeys.reviewWaitingAll() }); },",
      "});",
      "const report = useMutation({",
      "  mutationFn: (b) => reportDeidentMiss(id, b),",
      "  onSuccess: () => invalidateHolding(qc, \"review\"),",
      "});",
    ].join("\n");
    expect(missingHoldingRefresh("x.tsx", text)).toEqual(["x.tsx:1: decideReview"]);
  });

  it("src 전체에 그런 뮤테이션이 없다", () => {
    const hits = sourceFiles(SRC).flatMap((p) => missingHoldingRefresh(relative(SRC, p), readFileSync(p, "utf-8")));
    expect(hits).toEqual([]);
  });
});
