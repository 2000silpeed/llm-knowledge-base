"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import * as d3 from "d3";
import type { GraphData, GraphNode } from "@/lib/wiki";

interface SimNode extends d3.SimulationNodeDatum, GraphNode {}
interface SimEdge extends d3.SimulationLinkDatum<SimNode> {
  sourceSlug: string;
  targetSlug: string;
  relationType?: string;
}

const EDGE_COLORS: Record<string, string> = {
  parent: "#3b82f6",   // blue — 상위
  child: "#10b981",    // green — 하위
  related: "#9ca3af",  // gray — 연관
  conflict: "#ef4444", // red — 상충
};

export default function ConceptGraph() {
  const svgRef = useRef<SVGSVGElement>(null);
  const [data, setData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const router = useRouter();

  useEffect(() => {
    fetch("/api/graph")
      .then((r) => r.json())
      .then((d: GraphData) => {
        setData(d);
        setLoading(false);
      })
      .catch(() => {
        setError("그래프 데이터를 불러오지 못했습니다.");
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!data || !svgRef.current) return;

    const el = svgRef.current;
    const { width, height } = el.getBoundingClientRect();
    const W = width || 900;
    const H = height || 600;

    // 초기화
    d3.select(el).selectAll("*").remove();

    const svg = d3
      .select(el)
      .attr("width", W)
      .attr("height", H);

    // 줌/팬
    const root = svg.append("g");
    svg.call(
      d3.zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.2, 4])
        .on("zoom", (event) => root.attr("transform", event.transform))
    );

    // 노드/엣지 복사 (d3 mutation)
    const nodes: SimNode[] = data.nodes.map((n) => ({ ...n }));
    const slugToNode = new Map(nodes.map((n) => [n.id, n]));

    const edges: SimEdge[] = data.edges
      .map((e) => ({
        source: slugToNode.get(e.source) ?? e.source,
        target: slugToNode.get(e.target) ?? e.target,
        sourceSlug: e.source,
        targetSlug: e.target,
        relationType: e.relationType,
      }))
      .filter((e) => typeof e.source !== "string" && typeof e.target !== "string");

    // 연결 수 기반 노드 반지름
    const maxLinks = Math.max(1, ...nodes.map((n) => n.linkCount));
    const radius = (n: SimNode) => 6 + (n.linkCount / maxLinks) * 14;

    // 색상
    const color = (n: SimNode) =>
      n.group === "concept" ? "#3b82f6" : "#8b5cf6";

    // 포스 시뮬레이션
    const simulation = d3
      .forceSimulation<SimNode>(nodes)
      .force(
        "link",
        d3
          .forceLink<SimNode, SimEdge>(edges)
          .id((d) => d.id)
          .distance(80)
      )
      .force("charge", d3.forceManyBody().strength(-200))
      .force("center", d3.forceCenter(W / 2, H / 2))
      .force("collision", d3.forceCollide<SimNode>().radius((d) => radius(d) + 4));

    // 엣지
    const link = root
      .append("g")
      .attr("stroke-opacity", 0.6)
      .selectAll<SVGLineElement, SimEdge>("line")
      .data(edges)
      .join("line")
      .attr("stroke", (d) => EDGE_COLORS[d.relationType ?? ""] ?? "#d1d5db")
      .attr("stroke-width", (d) => d.relationType ? 1.6 : 1.2)
      .attr("stroke-dasharray", (d) => d.relationType === "conflict" ? "4,3" : null);

    // 노드 그룹
    const node = root
      .append("g")
      .selectAll<SVGGElement, SimNode>("g")
      .data(nodes)
      .join("g")
      .style("cursor", "pointer")
      .call(
        d3
          .drag<SVGGElement, SimNode>()
          .on("start", (event, d) => {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on("drag", (event, d) => {
            d.fx = event.x;
            d.fy = event.y;
          })
          .on("end", (event, d) => {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          })
      );

    // 원
    node
      .append("circle")
      .attr("r", (d) => radius(d))
      .attr("fill", (d) => color(d))
      .attr("fill-opacity", 0.85)
      .attr("stroke", "#fff")
      .attr("stroke-width", 1.5);

    // 레이블
    node
      .append("text")
      .text((d) => (d.title.length > 14 ? d.title.slice(0, 13) + "…" : d.title))
      .attr("x", (d) => radius(d) + 4)
      .attr("y", "0.35em")
      .attr("font-size", "11px")
      .attr("fill", "#374151")
      .style("pointer-events", "none")
      .style("user-select", "none");

    // 툴팁 효과
    node
      .on("mouseenter", (_event, d) => {
        setHovered(d.id);
        d3.select(_event.currentTarget as SVGGElement)
          .select("circle")
          .attr("stroke", "#1d4ed8")
          .attr("stroke-width", 2.5);
      })
      .on("mouseleave", (_event) => {
        setHovered(null);
        d3.select(_event.currentTarget as SVGGElement)
          .select("circle")
          .attr("stroke", "#fff")
          .attr("stroke-width", 1.5);
      })
      .on("click", (_event, d) => {
        if (d.group === "concept") {
          router.push(`/concepts/${encodeURIComponent(d.id)}`);
        } else {
          router.push(`/explorations/${encodeURIComponent(d.id)}`);
        }
      });

    // tick
    simulation.on("tick", () => {
      link
        .attr("x1", (d) => (d.source as SimNode).x ?? 0)
        .attr("y1", (d) => (d.source as SimNode).y ?? 0)
        .attr("x2", (d) => (d.target as SimNode).x ?? 0)
        .attr("y2", (d) => (d.target as SimNode).y ?? 0);

      node.attr("transform", (d) => `translate(${d.x ?? 0},${d.y ?? 0})`);
    });

    return () => {
      simulation.stop();
    };
  }, [data, router]);

  if (loading) return <p className="text-gray-400 text-sm">그래프 로딩 중…</p>;
  if (error) return <p className="text-red-500 text-sm">{error}</p>;
  if (!data || data.nodes.length === 0)
    return <p className="text-gray-400 text-sm">아직 개념이 없습니다.</p>;

  return (
    <div className="relative w-full" style={{ height: "calc(100vh - 160px)" }}>
      {/* 범례 */}
      <div className="absolute top-3 right-3 bg-white/90 border border-gray-200 rounded-lg px-3 py-2 text-xs text-gray-600 space-y-1 z-10">
        <div className="font-semibold text-gray-700 mb-1">노드</div>
        <div className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-full bg-blue-500" />
          개념 (concepts)
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-full bg-violet-500" />
          탐색 (explorations)
        </div>
        <div className="font-semibold text-gray-700 mt-2 mb-1">관계 유형</div>
        <div className="flex items-center gap-2">
          <span className="inline-block w-5 h-0.5 bg-blue-500" />
          상위 (parent)
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-block w-5 h-0.5 bg-emerald-500" />
          하위 (child)
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-block w-5 h-0.5 bg-gray-400" />
          연관 (related)
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-block w-5 border-t-2 border-dashed border-red-500" />
          상충 (conflict)
        </div>
        <div className="text-gray-400 mt-1">스크롤: 줌 · 드래그: 이동</div>
      </div>

      {/* 호버 툴팁 */}
      {hovered && (
        <div className="absolute bottom-3 left-3 bg-white/90 border border-gray-200 rounded-lg px-3 py-1.5 text-xs text-gray-700 z-10">
          {data.nodes.find((n) => n.id === hovered)?.title ?? hovered}
        </div>
      )}

      {/* 통계 */}
      <div className="absolute top-3 left-3 text-xs text-gray-400 z-10">
        노드 {data.nodes.length}개 · 연결 {data.edges.length}개
      </div>

      <svg
        ref={svgRef}
        className="w-full h-full bg-gray-50 rounded-xl border border-gray-200"
      />
    </div>
  );
}
