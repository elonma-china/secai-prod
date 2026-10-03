import type { CSSProperties, ReactNode } from "react";

/**
 * Industry DS "blueprint" frame: a hairline border plus four corner "+" registration marks
 * (`_ds/industry-…/styles.css` `.blueprint` + `.corner`). Used to accent the pieces of the UI
 * that most deserve the "wireframe object" treatment (the CVE result card, banners) without
 * overriding the IAS chrome's own rounded corners — pass `className="iascard"` alongside to
 * keep the panel look and just add the marks.
 */
export default function Blueprint({
  children,
  className = "",
  style,
}: {
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <div className={`blueprint ${className}`.trim()} style={style}>
      <i className="corner tl" aria-hidden="true" />
      <i className="corner tr" aria-hidden="true" />
      <i className="corner bl" aria-hidden="true" />
      <i className="corner br" aria-hidden="true" />
      {children}
    </div>
  );
}
