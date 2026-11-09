"use client";
import { useEffect, useRef } from "react";

/** The Cat-Egory mascot (same drawing as the project website). Its eyes follow the pointer unless the
 *  visitor prefers reduced motion. Decorative: hidden from assistive technology. */
export default function Mascot() {
  const ref = useRef<SVGSVGElement>(null);
  useEffect(() => {
    const svg = ref.current;
    if (!svg || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const pupils = Array.from(svg.querySelectorAll<SVGGElement>(".pupil"));
    let frame = 0;
    const move = (e: PointerEvent) => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const box = svg.getBoundingClientRect();
        const scale = box.width / 360;
        for (const p of pupils) {
          const ex = box.left + Number(p.dataset.x) * scale;
          const ey = box.top + Number(p.dataset.y) * scale;
          const a = Math.atan2(e.clientY - ey, e.clientX - ex);
          const d = Math.min(9, Math.hypot(e.clientX - ex, e.clientY - ey) / 30);
          p.setAttribute("transform", `translate(${(Math.cos(a) * d).toFixed(1)} ${(Math.sin(a) * d).toFixed(1)})`);
        }
      });
    };
    window.addEventListener("pointermove", move, { passive: true });
    return () => { window.removeEventListener("pointermove", move); cancelAnimationFrame(frame); };
  }, []);

  return (
    <figure className="mascot" aria-hidden="true">
      <svg ref={ref} className="cat" viewBox="0 0 360 360">
        <defs>
          <clipPath id="cat-head-clip"><ellipse cx="180" cy="160" rx="102" ry="86" /></clipPath>
          <clipPath id="cat-body-clip"><ellipse cx="180" cy="292" rx="88" ry="56" /></clipPath>
        </defs>
        <circle className="halo" cx="180" cy="190" r="165" />
        <path className="tail-outline" d="M122 304c-24 0-44-8-54-28" />
        <path className="tail-fill" d="M122 304c-24 0-44-8-54-28" />
        <ellipse className="fur" cx="180" cy="292" rx="88" ry="56" />
        <path className="white" clipPath="url(#cat-body-clip)" d="M126 262c14-24 94-24 108 0 4 40-20 74-54 82-34-8-58-42-54-82z" />
        <ellipse className="outline" cx="180" cy="292" rx="88" ry="56" />
        <rect className="collar" x="116" y="232" width="128" height="18" rx="9" />
        <circle className="tag" cx="180" cy="258" r="12" />
        <text className="tag-q" x="180" y="263" textAnchor="middle">?</text>
        <g className="head">
          <polygon className="fur" points="90,130 76,38 150,90" />
          <polygon className="ear-in" points="98,114 90,60 134,92" />
          <polygon className="fur" points="270,130 284,38 210,90" />
          <polygon className="ear-in" points="262,114 270,60 226,92" />
          <ellipse className="fur" cx="180" cy="160" rx="102" ry="86" />
          <path className="white" clipPath="url(#cat-head-clip)"
                d="M180 140c-6 14-9 30-22 42-18 14-42 26-50 56h144c-8-30-32-42-50-56-13-12-16-28-22-42z" />
          <ellipse className="outline" cx="180" cy="160" rx="102" ry="86" />
          <circle className="cheek" cx="112" cy="190" r="13" />
          <circle className="cheek" cx="248" cy="190" r="13" />
          <circle className="eye" cx="140" cy="152" r="27" />
          <circle className="eye" cx="220" cy="152" r="27" />
          <g className="pupil" data-x="140" data-y="152">
            <circle className="iris" cx="140" cy="152" r="13" />
            <circle className="glint" cx="135" cy="146" r="4.5" />
          </g>
          <g className="pupil" data-x="220" data-y="152">
            <circle className="iris" cx="220" cy="152" r="13" />
            <circle className="glint" cx="215" cy="146" r="4.5" />
          </g>
          <ellipse className="lid" cx="140" cy="152" rx="29" ry="29" />
          <ellipse className="lid" cx="220" cy="152" rx="29" ry="29" />
          <path className="nose" d="M171 186h18l-9 10z" />
          <path className="line" d="M180 196q-8 13-19 5M180 196q8 13 19 5" />
          <path className="whisker" d="M98 186 46 176M98 197l-52 6M262 186l52-10M262 197l52 6" />
        </g>
        <line className="handle" x1="311" y1="283" x2="338" y2="322" />
        <circle className="lens" cx="288" cy="258" r="34" />
        <path className="lens-glint" d="M267 247a24 24 0 0 1 18-16" />
        <path className="arm-outline" d="M232 292 300 300" />
        <path className="arm-fill" d="M232 292 300 300" />
        <ellipse className="sock" cx="310" cy="300" rx="18" ry="14" />
        <text className="q" x="312" y="78">?</text>
      </svg>
    </figure>
  );
}
