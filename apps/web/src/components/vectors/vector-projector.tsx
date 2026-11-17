"use client";

import { useId, useMemo, useRef, useState } from "react";
import type { VectorCluster, VectorPoint, VectorPointType } from "@/lib/types";
import { filterVectorPoints, project3Dto2D } from "@/lib/vectors";

interface VectorProjectorProps {
  points: VectorPoint[];
  clusters: VectorCluster[];
  selectedClusterId?: string | null;
  onClusterSelect?: (clusterId: string | null) => void;
}

const TYPE_COLORS: Record<VectorPointType, { fill: string; stroke: string; label: string; badge: string }> = {
  query: {
    fill: "#38bdf8", // Blue (Sky 400)
    stroke: "#0284c7",
    label: "Queries",
    badge: "border-sky-800/80 bg-sky-950/60 text-sky-300",
  },
  document: {
    fill: "#34d399", // Green (Emerald 400)
    stroke: "#059669",
    label: "Documents",
    badge: "border-emerald-800/80 bg-emerald-950/60 text-emerald-300",
  },
  gap: {
    fill: "#ef4444", // Red / Amber (Red 500)
    stroke: "#f59e0b",
    label: "Knowledge Gaps",
    badge: "border-red-800/80 bg-red-950/60 text-red-300",
  },
};

export function VectorProjector({
  points,
  clusters,
  selectedClusterId: externalClusterId,
  onClusterSelect,
}: VectorProjectorProps) {
  const [internalClusterId, setInternalClusterId] = useState<string>("all");
  const [selectedType, setSelectedType] = useState<VectorPointType | "all">("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [projectionMode, setProjectionMode] = useState<"2d" | "3d">("2d");
  const [rotationYaw, setRotationYaw] = useState(35);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });
  const [hoveredPoint, setHoveredPoint] = useState<VectorPoint | null>(null);
  const [activePoint, setActivePoint] = useState<VectorPoint | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const svgMaskId = useId();

  const activeClusterFilter = externalClusterId ?? (internalClusterId === "all" ? undefined : internalClusterId);

  const handleClusterChange = (newClusterId: string) => {
    setInternalClusterId(newClusterId);
    if (onClusterSelect) {
      onClusterSelect(newClusterId === "all" ? null : newClusterId);
    }
  };

  // Filtered points
  const filteredPoints = useMemo(() => {
    return filterVectorPoints(points, {
      clusterId: activeClusterFilter,
      type: selectedType,
      search: searchQuery,
    });
  }, [points, activeClusterFilter, selectedType, searchQuery]);

  // Projected 2D coordinates
  const projectedData = useMemo(() => {
    const rawCoords = points.map((p) => {
      if (projectionMode === "3d") {
        return project3Dto2D({ x: p.x, y: p.y, z: p.z ?? 0 }, rotationYaw, 25);
      }
      return { x: p.x, y: p.y };
    });

    let minX = Infinity,
      maxX = -Infinity,
      minY = Infinity,
      maxY = -Infinity;

    for (const c of rawCoords) {
      if (c.x < minX) minX = c.x;
      if (c.x > maxX) maxX = c.x;
      if (c.y < minY) minY = c.y;
      if (c.y > maxY) maxY = c.y;
    }

    const pad = 40;
    const viewW = 800;
    const viewH = 500;
    const rangeX = maxX - minX || 1;
    const rangeY = maxY - minY || 1;

    const coordsMap = new Map<string, { cx: number; cy: number }>();
    points.forEach((p, i) => {
      const c = rawCoords[i];
      const cx = pad + ((c.x - minX) / rangeX) * (viewW - pad * 2);
      const cy = pad + ((c.y - minY) / rangeY) * (viewH - pad * 2);
      coordsMap.set(p.id, { cx, cy });
    });

    return coordsMap;
  }, [points, projectionMode, rotationYaw]);

  // Point counts for summary
  const queryCount = useMemo(() => points.filter((p) => p.type === "query").length, [points]);
  const docCount = useMemo(() => points.filter((p) => p.type === "document").length, [points]);
  const gapCount = useMemo(() => points.filter((p) => p.type === "gap").length, [points]);

  // Zoom controls
  const handleZoomIn = () => setZoom((z) => Math.min(z + 0.25, 3.5));
  const handleZoomOut = () => setZoom((z) => Math.max(z - 0.25, 0.6));
  const handleReset = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
    setRotationYaw(35);
    setActivePoint(null);
  };

  // Pan handlers
  const handleMouseDown = (e: React.MouseEvent<SVGSVGElement>) => {
    if (e.button !== 0) return;
    setIsPanning(true);
    setPanStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
  };

  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!isPanning) return;
    setPan({
      x: e.clientX - panStart.x,
      y: e.clientY - panStart.y,
    });
  };

  const handleMouseUp = () => setIsPanning(false);

  return (
    <div className="flex flex-col rounded-lg border border-zinc-800 bg-zinc-900/60 shadow-lg">
      {/* Top Header & Interactive Controls Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs font-semibold uppercase tracking-wider text-zinc-400">
            Vector Projector
          </span>
          <span className="text-zinc-600">/</span>

          {/* 2D vs 3D Projection Toggle */}
          <div className="inline-flex rounded-md border border-zinc-700/80 bg-zinc-950 p-0.5">
            <button
              type="button"
              onClick={() => setProjectionMode("2d")}
              className={`rounded px-2.5 py-1 font-mono text-xs font-medium transition-colors ${
                projectionMode === "2d" ? "bg-zinc-800 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              2D UMAP
            </button>
            <button
              type="button"
              onClick={() => setProjectionMode("3d")}
              className={`rounded px-2.5 py-1 font-mono text-xs font-medium transition-colors ${
                projectionMode === "3d" ? "bg-zinc-800 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              3D Orbit
            </button>
          </div>

          {/* 3D Yaw Rotation Slider */}
          {projectionMode === "3d" && (
            <div className="flex items-center gap-1.5 pl-2 text-xs text-zinc-400">
              <span className="font-mono text-[11px] text-zinc-500">Yaw:</span>
              <input
                type="range"
                min="0"
                max="360"
                value={rotationYaw}
                onChange={(e) => setRotationYaw(Number(e.target.value))}
                aria-label="3D Yaw rotation"
                className="h-1.5 w-20 cursor-pointer accent-sky-400"
              />
              <span className="font-mono text-[11px] tabular-nums text-zinc-300">{rotationYaw}°</span>
            </div>
          )}
        </div>

        {/* Right Controls: Filters & Search */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Cluster Filter */}
          <select
            value={activeClusterFilter ?? "all"}
            onChange={(e) => handleClusterChange(e.target.value)}
            aria-label="Filter by cluster"
            className="rounded border border-zinc-700 bg-zinc-950 px-2.5 py-1 text-xs text-zinc-200 focus:border-zinc-500 focus:outline-none"
          >
            <option value="all">All Clusters ({clusters.length})</option>
            {clusters.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>

          {/* Type Filter */}
          <div className="inline-flex rounded-md border border-zinc-700/80 bg-zinc-950 p-0.5">
            <button
              type="button"
              onClick={() => setSelectedType("all")}
              className={`rounded px-2 py-0.5 font-mono text-[11px] ${
                selectedType === "all" ? "bg-zinc-800 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              All
            </button>
            <button
              type="button"
              onClick={() => setSelectedType("query")}
              className={`rounded px-2 py-0.5 font-mono text-[11px] ${
                selectedType === "query" ? "bg-sky-950/80 text-sky-300 font-semibold" : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Queries
            </button>
            <button
              type="button"
              onClick={() => setSelectedType("document")}
              className={`rounded px-2 py-0.5 font-mono text-[11px] ${
                selectedType === "document"
                  ? "bg-emerald-950/80 text-emerald-300 font-semibold"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Docs
            </button>
            <button
              type="button"
              onClick={() => setSelectedType("gap")}
              className={`rounded px-2 py-0.5 font-mono text-[11px] ${
                selectedType === "gap" ? "bg-red-950/80 text-red-300 font-semibold" : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Gaps
            </button>
          </div>

          {/* Search Input */}
          <input
            type="search"
            placeholder="Search vectors..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-36 rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-200 placeholder-zinc-500 focus:border-zinc-500 focus:outline-none"
          />
        </div>
      </div>

      {/* Main Canvas / SVG Projection Viewport */}
      <div
        ref={containerRef}
        className="relative h-[480px] w-full overflow-hidden bg-zinc-950/90 select-none cursor-grab active:cursor-grabbing"
      >
        {/* Subtle grid background */}
        <div
          aria-hidden
          className="absolute inset-0 bg-[radial-gradient(#27272a_1px,transparent_1px)] [background-size:24px_24px] opacity-40 pointer-events-none"
        />

        {/* Floating Zoom & Reset Toolbar */}
        <div className="absolute right-3 top-3 z-20 flex flex-col gap-1 rounded-md border border-zinc-800 bg-zinc-900/90 p-1 shadow-md">
          <button
            type="button"
            onClick={handleZoomIn}
            title="Zoom In"
            aria-label="Zoom in"
            className="flex h-7 w-7 items-center justify-center rounded text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100"
          >
            +
          </button>
          <button
            type="button"
            onClick={handleZoomOut}
            title="Zoom Out"
            aria-label="Zoom out"
            className="flex h-7 w-7 items-center justify-center rounded text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100"
          >
            −
          </button>
          <button
            type="button"
            onClick={handleReset}
            title="Reset View"
            aria-label="Reset zoom and position"
            className="flex h-7 w-7 items-center justify-center rounded font-mono text-[10px] text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
          >
            1x
          </button>
        </div>

        {/* Legend Overlay at bottom-left */}
        <div className="absolute bottom-3 left-3 z-20 flex items-center gap-3 rounded-md border border-zinc-800/90 bg-zinc-900/95 px-3 py-1.5 text-xs shadow-md">
          <div className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-sky-400 shadow-[0_0_8px_rgba(56,189,248,0.6)]" />
            <span className="text-zinc-300">Queries ({queryCount})</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]" />
            <span className="text-zinc-300">Docs ({docCount})</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-red-400 shadow-[0_0_8px_rgba(239,68,68,0.8)] animate-pulse" />
            <span className="font-medium text-red-300">Gaps ({gapCount})</span>
          </div>
          <span className="text-zinc-600">|</span>
          <span className="font-mono text-[11px] text-zinc-400">
            Visible: {filteredPoints.length}/{points.length}
          </span>
        </div>

        {/* SVG Drawing Canvas */}
        <svg
          className="h-full w-full"
          viewBox="0 0 800 500"
          preserveAspectRatio="xMidYMid meet"
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        >
          <defs>
            <filter id={`glow-${svgMaskId}`} x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feComposite in="SourceGraphic" in2="blur" operator="over" />
            </filter>
          </defs>

          {/* Group transformed by Zoom & Pan */}
          <g transform={`translate(${pan.x}, ${pan.y}) scale(${zoom})`} transform-origin="400 250">
            {/* Cluster Hull Guides / Centroid Badges */}
            {clusters.map((cluster) => {
              const clusterPts = points.filter((p) => p.cluster_id === cluster.id);
              if (!clusterPts.length) return null;

              // Centroid projection
              let avgX = 0,
                avgY = 0;
              let count = 0;
              clusterPts.forEach((p) => {
                const c = projectedData.get(p.id);
                if (c) {
                  avgX += c.cx;
                  avgY += c.cy;
                  count++;
                }
              });

              if (count === 0) return null;
              avgX /= count;
              avgY /= count;

              const isClusterActive = !activeClusterFilter || activeClusterFilter === cluster.id;

              return (
                <g key={cluster.id} opacity={isClusterActive ? 0.85 : 0.25} className="pointer-events-none transition-opacity">
                  {/* Subtle cluster halo */}
                  <ellipse
                    cx={avgX}
                    cy={avgY}
                    rx={75}
                    ry={55}
                    fill="none"
                    stroke="#3f3f46"
                    strokeWidth="1"
                    strokeDasharray="3 3"
                  />
                  <text
                    x={avgX}
                    y={avgY - 60}
                    textAnchor="middle"
                    fill="#a1a1aa"
                    fontSize="11"
                    fontFamily="monospace"
                    className="font-medium"
                  >
                    {cluster.name}
                  </text>
                </g>
              );
            })}

            {/* Connecting lines for Knowledge Gaps to nearest document */}
            {filteredPoints
              .filter((p) => p.type === "gap")
              .map((gapPoint) => {
                const gapCoord = projectedData.get(gapPoint.id);
                if (!gapCoord) return null;

                // Find nearest document in the same cluster
                const docsInCluster = points.filter(
                  (p) => p.type === "document" && p.cluster_id === gapPoint.cluster_id,
                );
                if (!docsInCluster.length) return null;

                const nearestDoc = docsInCluster[0];
                const docCoord = projectedData.get(nearestDoc.id);
                if (!docCoord) return null;

                return (
                  <line
                    key={`line-${gapPoint.id}`}
                    x1={gapCoord.cx}
                    y1={gapCoord.cy}
                    x2={docCoord.cx}
                    y2={docCoord.cy}
                    stroke="#ef4444"
                    strokeWidth="1.2"
                    strokeDasharray="4 4"
                    opacity="0.45"
                  />
                );
              })}

            {/* Render Vector Points */}
            {filteredPoints.map((point) => {
              const coord = projectedData.get(point.id);
              if (!coord) return null;

              const isGap = point.type === "gap";
              const isQuery = point.type === "query";
              const isSelected = activePoint?.id === point.id;
              const isHovered = hoveredPoint?.id === point.id;
              const style = TYPE_COLORS[point.type];

              const radius = isSelected ? 8 : isHovered ? 7 : isGap ? 6 : isQuery ? 4.5 : 5;

              return (
                <g
                  key={point.id}
                  className="cursor-pointer transition-transform"
                  onMouseEnter={() => setHoveredPoint(point)}
                  onMouseLeave={() => setHoveredPoint(null)}
                  onClick={(e) => {
                    e.stopPropagation();
                    setActivePoint(point);
                  }}
                >
                  {/* Outer pulse aura for Knowledge Gaps */}
                  {isGap && (
                    <circle
                      cx={coord.cx}
                      cy={coord.cy}
                      r={radius + 6}
                      fill="none"
                      stroke="#ef4444"
                      strokeWidth="1.5"
                      opacity="0.5"
                      className="animate-ping"
                    />
                  )}

                  {/* Highlight ring if active or hovered */}
                  {(isSelected || isHovered) && (
                    <circle
                      cx={coord.cx}
                      cy={coord.cy}
                      r={radius + 4}
                      fill="none"
                      stroke="#ffffff"
                      strokeWidth="1.5"
                      opacity="0.8"
                    />
                  )}

                  {/* Main Point Circle */}
                  <circle
                    cx={coord.cx}
                    cy={coord.cy}
                    r={radius}
                    fill={style.fill}
                    stroke={style.stroke}
                    strokeWidth={isSelected ? 2.5 : 1.5}
                    filter={isGap ? `url(#glow-${svgMaskId})` : undefined}
                  />
                </g>
              );
            })}
          </g>
        </svg>

        {/* Hover Tooltip Overlay */}
        {hoveredPoint && (
          <div
            className="pointer-events-none absolute left-4 top-4 z-30 max-w-sm rounded-md border border-zinc-700 bg-zinc-900/95 p-2.5 shadow-xl backdrop-blur"
            style={{ pointerEvents: "none" }}
          >
            <div className="flex items-center gap-2">
              <span
                className={`rounded border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider ${
                  TYPE_COLORS[hoveredPoint.type].badge
                }`}
              >
                {hoveredPoint.type}
              </span>
              <span className="font-mono text-xs font-medium text-zinc-200">
                {hoveredPoint.cluster_name ?? hoveredPoint.cluster_id}
              </span>
              {hoveredPoint.score !== undefined && (
                <span className="ml-auto font-mono text-xs tabular-nums text-emerald-400">
                  score: {hoveredPoint.score.toFixed(2)}
                </span>
              )}
            </div>
            <div className="mt-1.5 text-xs font-medium text-zinc-100">{hoveredPoint.label}</div>
            {hoveredPoint.preview && (
              <div className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-zinc-400">
                {hoveredPoint.preview}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Selected Point Inspector Drawer */}
      {activePoint && (
        <div className="border-t border-zinc-800 bg-zinc-950/90 px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <span
                  className={`rounded border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase ${
                    TYPE_COLORS[activePoint.type].badge
                  }`}
                >
                  {activePoint.type}
                </span>
                <span className="text-xs font-semibold text-zinc-100">{activePoint.label}</span>
                <span className="text-xs text-zinc-500">•</span>
                <span className="text-xs text-zinc-400">{activePoint.cluster_name}</span>
              </div>
              {activePoint.preview && (
                <p className="mt-1.5 max-w-3xl text-xs leading-relaxed text-zinc-300">
                  {activePoint.preview}
                </p>
              )}
              <div className="mt-2 flex flex-wrap items-center gap-4 text-xs text-zinc-500">
                <span>
                  ID: <code className="font-mono text-zinc-400">{activePoint.id}</code>
                </span>
                {activePoint.trace_id && (
                  <span>
                    Trace: <code className="font-mono text-emerald-400">{activePoint.trace_id}</code>
                  </span>
                )}
                {activePoint.document_id && (
                  <span>
                    Doc: <code className="font-mono text-emerald-400">{activePoint.document_id}</code>
                  </span>
                )}
                {activePoint.score !== undefined && (
                  <span>
                    Score:{" "}
                    <span className="font-mono font-medium text-zinc-200">
                      {activePoint.score.toFixed(3)}
                    </span>
                  </span>
                )}
                <span>
                  Coord:{" "}
                  <span className="font-mono text-zinc-400">
                    ({activePoint.x}, {activePoint.y}
                    {activePoint.z !== undefined ? `, ${activePoint.z}` : ""})
                  </span>
                </span>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setActivePoint(null)}
              className="rounded p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
              aria-label="Close details"
            >
              ✕
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
