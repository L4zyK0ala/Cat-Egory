/** A small "i" that explains the thing next to it: on hover, keyboard focus or tap (pure CSS, works without
 *  JavaScript). ``end`` anchors the bubble to the right, for items near the right edge. */
export default function Hint({ text, end = false }: { text: string; end?: boolean }) {
  return (
    <span className={`hint${end ? " end" : ""}`} tabIndex={0} role="button" aria-label={text}>
      <span aria-hidden="true">i</span>
      <span className="hint-bubble" role="tooltip">{text}</span>
    </span>
  );
}
