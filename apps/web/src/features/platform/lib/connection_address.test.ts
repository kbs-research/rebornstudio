/**
 * 연결 편집 창이 주소를 바꿀 때 키 입력란을 필수로 표시하는 판정이 서버와 같은지 확인한다.
 *
 * 판정 표(`packages/reborn-contracts/connection-address.json`)를 서버 테스트
 * (`apps/api-gateway/tests/contract/test_connection_address.py`)와 함께 읽는다.
 * 한쪽만 실패하면 화면은 저장 버튼을 열어 두는데 서버가 422로 거절하는 상태다.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { ConnectionProvider } from "@/shared/api/client";

import { bucketListText, parseBucketList, ROUTING_KEYS, routingChanges, secretRequired } from "./connection_address";

type Case = {
  why: string;
  provider: ConnectionProvider;
  has_secret: boolean;
  secret_sent: boolean;
  clear_secret: boolean;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  required: boolean;
};
type Table = { routing_keys: Record<string, string[]>; cases: Case[] };

const table = JSON.parse(
  readFileSync(
    resolve(
      fileURLToPath(new URL(".", import.meta.url)),
      "../../../../../../packages/reborn-contracts/connection-address.json",
    ),
    "utf-8",
  ),
) as Table;

describe("판정 표", () => {
  it("비어 있지 않고 두 답이 모두 나온다", () => {
    expect(table.cases.length).toBeGreaterThanOrEqual(10);
    expect(new Set(table.cases.map((c) => c.required))).toEqual(new Set([true, false]));
  });

  it("주소 키 표가 서버와 같다", () => {
    expect(ROUTING_KEYS).toEqual(table.routing_keys);
  });
});

describe("secretRequired는 서버 secret_required와 같은 답을 낸다", () => {
  for (const c of table.cases) {
    it(c.why, () => {
      expect(secretRequired({
        provider: c.provider, hasSecret: c.has_secret, secretSent: c.secret_sent,
        clearSecret: c.clear_secret, before: c.before, after: c.after,
      })).toBe(c.required);
    });
  }
});

describe("편집 창의 키 칸 필수 표시", () => {
  const saved = { endpoint: "https://kr.object.ncloudstorage.com", bucket: "sample-bucket", region: "kr-standard" };

  it("엔드포인트를 고치면 키를 넣기 전까지 필수이고, 넣으면 풀린다", () => {
    const edited = { ...saved, endpoint: "https://other.example.com" };
    const base = { provider: "ncp" as const, hasSecret: true, clearSecret: false, before: saved, after: edited };
    expect(secretRequired({ ...base, secretSent: false })).toBe(true);
    expect(secretRequired({ ...base, secretSent: true })).toBe(false);
    expect(routingChanges("ncp", saved, edited)).toEqual(["endpoint"]);
  });

  it("저장소나 허용 저장소만 고치면 필수가 아니다", () => {
    const edited = { ...saved, bucket: "sample-bucket-2", allowed_buckets: ["sample-bucket-2", "sample-bucket-3"] };
    expect(secretRequired({
      provider: "ncp", hasSecret: true, secretSent: false, clearSecret: false, before: saved, after: edited,
    })).toBe(false);
  });

  it("입력란이 글자로 들고 있는 같은 주소는 바뀐 것이 아니다", () => {
    const edited = { ...saved, endpoint: "https://kr.object.ncloudstorage.com/" };
    expect(routingChanges("ncp", saved, edited)).toEqual([]);
  });
});

describe("허용 저장소 입력란", () => {
  it("쉼표·공백·줄바꿈으로 나누고 빈 항목을 뺀다", () => {
    expect(parseBucketList(" a-1, b-2\nc-3 ,, ")).toEqual(["a-1", "b-2", "c-3"]);
    expect(parseBucketList("")).toEqual([]);
  });

  it("저장된 목록을 쉼표로 이어 보여 주고 목록이 아니면 비운다", () => {
    expect(bucketListText(["a-1", "b-2"])).toBe("a-1, b-2");
    expect(bucketListText(undefined)).toBe("");
  });
});
