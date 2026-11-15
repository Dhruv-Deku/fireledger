// zrender (the renderer under ECharts) can finish a slow paint on the next
// animation frame. If the chart is disposed before that frame runs (React's
// dev double-mount, or closing the results panel mid-paint), the queued frame
// reads the cleared painter and throws "Cannot read properties of null
// (reading 'layerStack')". Skip queued paints for disposed painters.
import CanvasPainter from "zrender/lib/canvas/Painter";

type Guarded = {
  _i: unknown;
  _paintList: (...args: unknown[]) => unknown;
  __flDisposeGuard?: boolean;
};

const proto = CanvasPainter.prototype as unknown as Guarded;
if (!proto.__flDisposeGuard) {
  const paintList = proto._paintList;
  proto._paintList = function (this: Guarded, ...args: unknown[]) {
    if (!this._i) return; // painter was disposed: nothing left to draw on
    return paintList.apply(this, args);
  };
  proto.__flDisposeGuard = true;
}
