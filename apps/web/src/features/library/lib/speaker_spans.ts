/**
 * 화자별 발화 구간 모으기. 클립 미리보기의 화자 절이 쓴다.
 */

/** 발화 한 건. 서버가 분석 서비스의 값을 그대로 싣는다(`speeches`, ms 단위). */
export type Speech = { startMs?: number; endMs?: number; text?: string; pid?: string | number; name?: string };

/**
 * 화자별 발화 구간(첫 발화 시작부터 마지막 발화 끝까지)을 모은다.
 * 화자 절이 이름 옆에 발화 구간을 함께 보여 줄 때 쓴다. 발화가 없는 화자는 이름만 남긴다.
 */
export function speakerSpans(
  speakers: Array<Record<string, unknown>>,
  speeches: Array<Record<string, unknown>>,
): { name: string; start: number | null; end: number | null }[] {
  const spans = new Map<string, { name: string; start: number | null; end: number | null }>();
  for (const sp of speakers) {
    const name = String(sp.name ?? sp.pid ?? "화자");
    spans.set(String(sp.pid ?? name), { name, start: null, end: null });
  }
  for (const raw of speeches as Speech[]) {
    const key = String(raw.pid ?? raw.name ?? "");
    if (!key) continue;
    const cur = spans.get(key) ?? { name: String(raw.name ?? raw.pid), start: null, end: null };
    const s = typeof raw.startMs === "number" ? raw.startMs / 1000 : null;
    const e = typeof raw.endMs === "number" ? raw.endMs / 1000 : null;
    if (s != null) cur.start = cur.start == null ? s : Math.min(cur.start, s);
    if (e != null) cur.end = cur.end == null ? e : Math.max(cur.end, e);
    spans.set(key, cur);
  }
  return [...spans.values()];
}

