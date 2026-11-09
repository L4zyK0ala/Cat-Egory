import type React from "react";
import { useId } from "react";

/** Full-body scenes with Kostüm, the cat the mascot is drawn after: grey and white, a bit chubby, half a tail.
 *  Decorative only (hidden from assistive technology). Movement is pure CSS and stops when the visitor prefers
 *  reduced motion (globals.css, "cat scenes"). */

type Eyes = "open" | "happy" | "asleep";

/** The head, centred on (0, 0), facing the viewer. Place it with a transform. */
function Head({ eyes = "open" }: { eyes?: Eyes }) {
  const clip = useId();
  return (
    <g>
      <defs><clipPath id={clip}><ellipse cx="0" cy="0" rx="36" ry="30" /></clipPath></defs>
      <polygon className="fur" points="-31,-10 -29,-47 -5,-26" />
      <polygon className="ear-in" points="-26,-16 -25,-38 -11,-26" />
      <polygon className="fur" points="31,-10 29,-47 5,-26" />
      <polygon className="ear-in" points="26,-16 25,-38 11,-26" />
      <ellipse className="fur" cx="0" cy="0" rx="36" ry="30" />
      {/* Kostüm's white blaze: from the nose bridge down over the muzzle and chin */}
      <path className="white" clipPath={`url(#${clip})`}
            d="M0-12C-3-3-6 3-13 8-23 14-30 22-32 34H32C30 22 23 14 13 8 6 3 3-3 0-12Z" />
      <ellipse className="outline" cx="0" cy="0" rx="36" ry="30" />
      <circle className="cheek" cx="-22" cy="9" r="6" />
      <circle className="cheek" cx="22" cy="9" r="6" />
      {eyes === "open" ? (
        <>
          <circle className="eye" cx="-14" cy="-4" r="8.5" />
          <circle className="eye" cx="14" cy="-4" r="8.5" />
          <ellipse className="iris" cx="-13" cy="-4" rx="3.4" ry="6" />
          <ellipse className="iris" cx="15" cy="-4" rx="3.4" ry="6" />
          <circle className="glint" cx="-15" cy="-7" r="1.8" />
          <circle className="glint" cx="13" cy="-7" r="1.8" />
        </>
      ) : (
        <path className="line" d={eyes === "happy" ? "M-21-2q7-8 14 0M7-2q7-8 14 0" : "M-21-4q7 6 14 0M7-4q7 6 14 0"} />
      )}
      <path className="nose" d="M-5 7h10l-5 6z" />
      <path className="line" d="M0 13q-4 6-9 3M0 13q4 6 9 3" />
      <path className="whisker" d="M-24 9-50 4M-24 14-50 18M24 9 50 4M24 14 50 18" />
    </g>
  );
}

/** Rotate around a point in the drawing's own coordinates (a hip, the root of the tail). */
function pivot(x: number, y: number): React.CSSProperties {
  return { transformBox: "view-box", transformOrigin: `${x}px ${y}px` };
}

/** A leg as a thick rounded stroke with an outline, ending in a white sock. */
function Leg({ x1, y1, x2, y2, far = false, up = false, className = "" }: {
  x1: number; y1: number; x2: number; y2: number; far?: boolean; up?: boolean; className?: string;
}) {
  const d = `M${x1} ${y1}L${x2} ${y2}`;
  return (
    <g className={`leg ${className}`} style={pivot(x1, y1)}>
      <path className="limb-o" d={d} />
      <path className={far ? "limb far" : "limb"} d={d} />
      {up ? <circle className="paw" cx={x2} cy={y2} r="11" />  /* a paw in the air: round toe beans */
        : <ellipse className="paw" cx={x2 + 3} cy={y2 + 3} rx="12" ry="6.5" />}
    </g>
  );
}

/** Half a tail: a short stub with a rounded end (Kostüm's is about half the usual length), from (x1, y1) at
 *  the body to (x2, y2). */
function Stub({ x1, y1, x2, y2, className = "" }: { x1: number; y1: number; x2: number; y2: number; className?: string }) {
  const d = `M${x1} ${y1}L${x2} ${y2}`;
  return (
    <g className={`stub ${className}`} style={pivot(x1, y1)}>
      <path className="tail-o" d={d} />
      <path className="tail" d={d} />
    </g>
  );
}

function Body({ cx, cy, rx, ry, belly }: { cx: number; cy: number; rx: number; ry: number; belly: string }) {
  const clip = useId();
  return (
    <>
      <defs><clipPath id={clip}><ellipse cx={cx} cy={cy} rx={rx} ry={ry} /></clipPath></defs>
      <ellipse className="fur" cx={cx} cy={cy} rx={rx} ry={ry} />
      <path className="white" clipPath={`url(#${clip})`} d={belly} />
      <ellipse className="outline" cx={cx} cy={cy} rx={rx} ry={ry} />
    </>
  );
}

function Mouse() {
  return (
    <svg className="kitty mouse" viewBox="0 0 90 60">
      <path className="mouse-tail" d="M18 40C4 40 2 28 8 22" />
      <g className="mouse-run">
        <path className="limb-thin" d="M30 46l-4 8M50 46l4 8" />
        <ellipse className="mouse-body" cx="40" cy="36" rx="24" ry="15" />
        <circle className="mouse-ear" cx="56" cy="20" r="9" />
        <path className="mouse-body" d="M56 26c10 2 20 6 24 12-6 3-16 4-24 2z" />
        <circle className="iris" cx="66" cy="31" r="2.6" />
        <circle className="nose" cx="80" cy="38" r="3" />
        <path className="whisker thin" d="M76 36l10-5M76 39l11 1" />
      </g>
    </svg>
  );
}

/** Running after a mouse along the bottom of the box (used on the feeds page). */
export function CatChase() {
  return (
    <div className="cat-scene chase" aria-hidden="true">
      <div className="chase-runner">
        <svg className="kitty runner-cat" viewBox="0 0 260 170">
          <Leg x1={92} y1={122} x2={98} y2={152} far className="leg-a" />
          <Leg x1={150} y1={122} x2={156} y2={152} far className="leg-b" />
          <Stub x1={58} y1={92} x2={36} y2={72} className="wag" />
          <Body cx={118} cy={102} rx={66} ry={42} belly="M60 114c30 22 96 24 126 0v60H60z" />
          {/* white chest under the chin */}
          <path className="white" d="M164 86c14 4 20 20 18 34-10 6-22 6-30 0 0-14 4-28 12-34z" />
          <Leg x1={74} y1={126} x2={68} y2={154} className="leg-b" />
          <Leg x1={164} y1={126} x2={170} y2={154} className="leg-a" />
          <g transform="translate(196 70) rotate(6)"><Head /></g>
        </svg>
        <Mouse />
      </div>
    </div>
  );
}

/** Lying on its back, rolling from side to side to be petted (used on the about page). */
export function CatRoll() {
  return (
    <div className="cat-scene roll" aria-hidden="true">
      <svg className="kitty" viewBox="0 0 280 170">
        <text className="heart h1" x="120" y="34">♥</text>
        <text className="heart h2" x="170" y="26">♥</text>
        <g className="roller">
          <Stub x1={208} y1={118} x2={234} y2={128} className="wag" />
          <Leg x1={108} y1={94} x2={96} y2={60} up className="paw-wiggle" />
          <Leg x1={128} y1={88} x2={134} y2={56} up className="paw-wiggle late" />
          <Leg x1={176} y1={90} x2={184} y2={58} up className="paw-wiggle" />
          <Leg x1={194} y1={98} x2={212} y2={70} up className="paw-wiggle late" />
          {/* belly up: the white belly is on top */}
          <Body cx={150} cy={118} rx={70} ry={36} belly="M94 106a56 21 0 1 0 112 0a56 21 0 1 0-112 0z" />
          <g transform="translate(70 116) rotate(-28)"><Head eyes="happy" /></g>
        </g>
      </svg>
    </div>
  );
}

/** Curled up asleep, breathing slowly (used on the categories page). */
export function CatNap() {
  return (
    <div className="cat-scene nap" aria-hidden="true">
      <svg className="kitty" viewBox="0 0 280 170">
        <text className="zzz z1" x="214" y="52">z</text>
        <text className="zzz z2" x="230" y="34">z</text>
        <text className="zzz z3" x="248" y="18">Z</text>
        <g className="breathe">
          <Stub x1={52} y1={132} x2={28} y2={146} />
          <Body cx={124} cy={118} rx={80} ry={42} belly="M52 140c40 20 120 20 150 0v30H52z" />
          <ellipse className="paw" cx="168" cy="152" rx="14" ry="8" />
          <ellipse className="paw" cx="194" cy="154" rx="14" ry="8" />
          <g transform="translate(186 118) rotate(10)"><Head eyes="asleep" /></g>
        </g>
      </svg>
    </div>
  );
}

/** Sitting up, waiting politely, the half tail flicking (used on the submit page). */
export function CatSit() {
  return (
    <div className="cat-scene sit" aria-hidden="true">
      <svg className="kitty" viewBox="0 0 200 210">
        <Stub x1={60} y1={176} x2={32} y2={162} className="flick" />
        <Body cx={100} cy={150} rx={56} ry={50} belly="M70 110c10-10 50-10 60 0 10 30 6 70-30 92-36-22-40-62-30-92z" />
        <ellipse className="paw" cx="80" cy="198" rx="13" ry="8" />
        <ellipse className="paw" cx="120" cy="198" rx="13" ry="8" />
        <g className="tilt" transform="translate(100 74)"><g className="tilt-inner"><Head /></g></g>
      </svg>
    </div>
  );
}
