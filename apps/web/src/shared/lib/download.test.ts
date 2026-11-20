/** 납품 패키지 받기 테스트. 내용을 받아 저장하고, 받지 못하면 같은 창 이동으로 넘어가야 한다. */
import { afterEach, describe, expect, it, vi } from "vitest";

import { BLOB_URL_TTL_MS, downloadUrl } from "./download";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/** 테스트용 문서. 클릭된 링크의 주소와 저장 이름을 기록한다. */
function fakeDocument() {
  const clicked: { href: string; download: string }[] = [];
  const doc = {
    body: { appendChild: () => {} },
    createElement: () => {
      const el = {
        href: "", download: "", rel: "",
        click: () => clicked.push({ href: el.href, download: el.download }),
        remove: () => {},
      };
      return el;
    },
  };
  vi.stubGlobal("document", doc);
  return clicked;
}

/** 테스트용 object URL. 만든 주소와 해제한 주소를 기록한다. */
function fakeObjectUrls() {
  const revoked: string[] = [];
  vi.stubGlobal("URL", {
    createObjectURL: () => "blob:local/1",
    revokeObjectURL: (u: string) => revoked.push(u),
  });
  return revoked;
}

describe("downloadUrl", () => {
  it("내용을 받아 정한 이름으로 저장하고 object URL은 바로 해제하지 않는다", async () => {
    vi.useFakeTimers();
    const clicked = fakeDocument();
    const revoked = fakeObjectUrls();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new Blob(["zip"]), { status: 200 })));

    await downloadUrl("https://storage.example/p.zip?sig=1", "sample-20261006.zip");

    expect(clicked).toEqual([{ href: "blob:local/1", download: "sample-20261006.zip" }]);
    expect(revoked).toEqual([]);
    vi.advanceTimersByTime(BLOB_URL_TTL_MS);
    expect(revoked).toEqual(["blob:local/1"]);
  });

  it("받지 못하면(CORS·네트워크) 서명된 링크로 같은 창에서 연다", async () => {
    const clicked = fakeDocument();
    fakeObjectUrls();
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));

    await downloadUrl("https://storage.example/p.zip?sig=1", "p.zip");

    expect(clicked).toEqual([{ href: "https://storage.example/p.zip?sig=1", download: "" }]);
  });

  it("저장소가 오류로 답하면(만료 등) 서명된 링크로 연다", async () => {
    const clicked = fakeDocument();
    fakeObjectUrls();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("expired", { status: 403 })));

    await downloadUrl("https://storage.example/p.zip?sig=1", "p.zip");

    expect(clicked.map((c) => c.href)).toEqual(["https://storage.example/p.zip?sig=1"]);
  });
});
