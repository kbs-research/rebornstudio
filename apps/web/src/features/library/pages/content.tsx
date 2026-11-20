/**
 * 콘텐츠 화면(`/p/:slug/content`). 영상과 클립을 탭으로 나눠 보여 준다.
 *
 * 영상 탭은 원본 목록을 보여 주고, 행을 누르면 영상 상세로 간다.
 * 클립 탭은 장면 단위 갤러리·표와 분석 결과를 보여 준다.
 * 한 목록에 섞으면 영상 목록과 클립 검색 결과가 같은 모양이라 구분할 수 없다.
 */
import { useCallback } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Film, Scissors } from "lucide-react";

import { PageHeader } from "@/shared/ui/PageHeader";
import { Tabs } from "@/shared/ui/Tabs";

import { ClipsPanel } from "../components/ClipsPanel";
import { VideoListPage } from "../components/VideoListPage";

type Tab = "videos" | "clips";

/*
  탭에는 툴팁을 두지 않는다. 보이는 이름(`영상`·`클립`)이 곧 탭의 이름이다.
  툴팁을 두면 보조기기가 읽는 이름과 보이는 이름이 달라진다.
*/
const CHIPS: { key: Tab; label: string; Icon: typeof Film }[] = [
  { key: "videos", label: "영상", Icon: Film },
  { key: "clips", label: "클립", Icon: Scissors },
];

/** 영상·클립 탭을 전환하고, 영상을 고르면 영상 상세 페이지로 이동한다. */
export function ContentPage() {
  const [params, setParams] = useSearchParams();
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  /*
    탭은 주소에 둔다(`?tab=clips`). 그래서 새로고침·뒤로 가기에도 같은 탭이 열린다.
    `replace`라 탭을 오가도 뒤로 가기 이력이 쌓이지 않는다. 영상 탭의 검색·필터도 주소에 있다(`VideoListPage`).
  */
  const tab: Tab = params.get("tab") === "clips" ? "clips" : "videos";
  const setTab = useCallback(
    (k: Tab) =>
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        if (k === "clips") next.set("tab", "clips");
        else next.delete("tab");
        return next;
      }, { replace: true }),
    [setParams],
  );

  /* 영상 상세는 드로어 대신 전용 라우트(`p/:slug/videos/:videoId`)의 페이지로 연다.
     담을 내용이 드로어 폭에 맞지 않고, 페이지 이동이라 뒤로 가기 한 번으로 목록에 돌아온다. */
  const open = (id: string) => navigate(`/p/${slug}/videos/${id}`);

  return (
    <div className="space-y-4">
      {/* 페이지 머리 아래에 영상·클립 전환 탭을 둔다. 탭 이름이 내용을 보여 주므로 부제는 두지 않는다. */}
      <PageHeader title="콘텐츠" />

      <Tabs
        variant="underline"
        className="-mt-1"
        tabs={CHIPS.map(({ key, label, Icon }) => ({
          key,
          label: (
            <span className="inline-flex items-center gap-1.5">
              <Icon className="size-3.5 shrink-0" aria-hidden="true" />
              {label}
            </span>
          ),
        }))}
        value={tab}
        onChange={(k) => setTab(k as Tab)}
      />

      {tab === "videos" && <VideoListPage onOpenVideo={open} embedded />}
      {tab === "clips" && <ClipsPanel />}

    </div>
  );
}
