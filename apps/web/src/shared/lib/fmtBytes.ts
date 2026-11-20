/**
 * 바이트 수를 사람이 읽는 크기로 바꾼다. 버킷 목록과 영상 규격이 같은 문자열을 쓰게 한다.
 *
 * 같은 수가 화면마다 다른 모양이면(1.7 GB와 1782579200) 같은 값인지 눈으로 대조할 수 없다.
 */
export function fmtBytes(n: number): string {
  if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(1)} GB`;
  if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(1)} MB`;
  if (n >= 1 << 10) return `${(n / (1 << 10)).toFixed(0)} KB`;
  return `${n} B`;
}

/**
 * 비트레이트(bit/s)를 사람이 읽는 값으로 바꾼다. 1 Mbps 이상은 소수 한 자리 Mbps, 그 아래는 kbps다.
 *
 * 비트레이트는 관례대로 1000 단위(SI)로 나눈다. 1024로 나누는 `fmtBytes`와 단위가 다르다.
 * kbps로 반올림한 값이 1000 이상이면 Mbps로 써서 `1000 kbps` 같은 표기를 피한다.
 */
export function fmtBitRate(bps: number): string {
  const kbps = Math.round(bps / 1000);
  if (kbps >= 1000) return `${(bps / 1_000_000).toFixed(1)} Mbps`;
  return `${kbps} kbps`;
}

/**
 * ffprobe의 컨테이너 표기를 사람이 읽는 이름 하나로 바꾼다.
 *
 * `format_name`은 파일 포맷 대신 그 파일을 연 디먹서 이름이다.
 * ISO BMFF 계열은 디먹서 하나가 MOV·MP4·M4A·3GP를 모두 처리해서
 * 컨테이너가 하나여도 `mov,mp4,m4a,3gp,3g2,mj2`가 통째로 나온다.
 * 그대로 화면에 쓰면 개발자용 원문이 노출된다.
 *
 * 계열 안에서 실제로 무엇인지는 `ftyp`의 major brand로만 알 수 있다(`qt  `=MOV, `isom`/`mp4x`=MP4).
 * brand가 없으면(BMFF가 아니거나 brand를 남기지 않은 기록) 디먹서 이름의 첫 토큰을 쓴다.
 * mkv·webm은 목록이 짧아서 첫 토큰으로 충분하다.
 */
export function fmtContainer(
  formatName: string | null | undefined,
  majorBrand?: string | null,
): string | null {
  const brand = (majorBrand ?? "").trim().toLowerCase();
  if (brand) {
    if (brand === "qt") return "MOV";
    if (brand.startsWith("mp4") || brand === "isom" || brand === "avc1" || brand === "dash") {
      return "MP4";
    }
    if (brand.startsWith("3g")) return "3GP";
    return brand.toUpperCase();
  }
  const first = (formatName ?? "").split(",")[0]?.trim();
  if (!first) return null;
  return { matroska: "MKV", webm: "WEBM", mov: "MP4/MOV" }[first] ?? first.toUpperCase();
}
