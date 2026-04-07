// 클리퍼 전용 레이아웃 — 사이드바 없는 모바일 우선 레이아웃
export default function ClipperLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
