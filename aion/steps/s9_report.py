# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               9_report -- was any of this worth it?
#
#  Eight steps mine cells, prove them, draw them, harden a chip out of them
#  and take its picture.  None of them ever answers the question the flow
#  exists to ask: is the chip built from AI-generated cells better than the
#  same chip built from the PDK's, and by how much.
#
#  The comparison needs a second chip.  `make baseline` hardens the same
#  RTL through the same config with the PDK standard cells only, and that is
#  the control: same die, same SDC, same synthesis strategy, so the cell
#  substitution is the only variable and the deltas are attributable to it.
#  This step runs it when it is missing and reuses it when it is not.
#
#  The second half of the report is harsher and more useful.  Step 3 predicts
#  an area saving from AREA_FACTOR -- a guess made before a single cell was
#  drawn -- and step 6 then draws them, at whatever size they actually came
#  out.  Those two numbers disagree, and a flow that reports only the
#  prediction is a flow that reports its own assumptions back to itself.
# ================================================================

from __future__ import annotations

import json

from pathlib import Path

from .. import librelane, workspace
from ..runner import container_running
from ..style import Style, color, info, note, ok, warn
from .base import Context, Step, StepFailed

#: The baseline is not a step: nothing downstream consumes it.  It is the
#: control this one step measures against, saved in <run>/baseline/
#: (`make baseline` builds it on its own).


def rewrite_report(ctx) -> Path:
    return ctx.ws.step("3_rewrite") / "report" / "rewrite_report.json"

#: How a verdict is coloured in the terminal.
VERDICT_STYLE = {
    "win": (Style.GREEN, Style.BOLD),
    "loss": (Style.RED, Style.BOLD),
    "tie": (Style.DIM,),
    "clean": (Style.GREEN,),
    "fail": (Style.RED, Style.BOLD),
    "info": (Style.DIM,),
    "n/a": (Style.DIM,),
}


class ReportStep(Step):
    key = "9_report"
    title = "Report"
    summary = "compare the AION chip against the PDK-only baseline and say what it bought"
    upstream = ("3_rewrite", "6_layout_drawing", "7_pnr")
    # The baseline may have to be hardened, which needs the container -- but
    # only then. Declaring it here would demand a running container for every
    # re-report of a baseline that already exists, which is the common case,
    # so the step checks for itself at the point it actually needs one.
    needs_container = False

    def inputs(self, ctx: Context) -> list:
        return [ctx.ws.step("7_pnr") / "metrics.json",
                ctx.ws.baseline_dir / "metrics.json",
                rewrite_report(ctx)]

    def outputs(self, ctx: Context) -> list:
        return [self.outdir(ctx) / "report.md", self.outdir(ctx) / "report.json",
                self.outdir(ctx) / "report.html"]

    # -----------------------------------------------------------------
    def run(self, ctx: Context) -> str:
        cfg = ctx.cfg
        aion_metrics = ctx.ws.step("7_pnr") / "metrics.json"
        self.require(aion_metrics)

        self._ensure_baseline(ctx)
        self.ensure(ctx)
        if self.dry_run:
            info(f"would compare {workspace.rel(aion_metrics)} against "
                 f"{workspace.rel(ctx.ws.baseline_dir)}/metrics.json")
            return "ok"

        try:
            from compare_runs import compare, render_markdown
        except ImportError as exc:
            raise StepFailed(f"cannot import the comparison: {exc}", 2) from exc

        try:
            record = compare(ctx.ws.step("7_pnr"), ctx.ws.baseline_dir,
                             rewrite_report(ctx), ctx.ws.cells_dir,
                             prefix=cfg.CELL_PREFIX)
        except Exception as exc:            # noqa: BLE001 - reported as a failure
            raise StepFailed(f"comparison failed: {exc}", 2) from exc

        markdown = self.outdir(ctx) / "report.md"
        markdown.write_text(render_markdown(record))
        (self.outdir(ctx) / "report.json").write_text(json.dumps(record, indent=2) + "\n")
        page = self._write_page(ctx, record, ctx.ws.design.top)

        self._report(ctx, record, page)
        return "ok"

    # -----------------------------------------------------------------
    def _write_page(self, ctx: Context, record: dict, top: str):
        """The same record as a page you can look at.

        Never allowed to fail the step: the report is report.md and report.json,
        and a host that cannot render the page still has both.
        """
        try:
            from report_html import render_html
        except ImportError as exc:
            warn(f"no HTML page — {exc}")
            return None
        try:
            page = self.outdir(ctx) / "report.html"
            page.write_text(
                render_html(record, images=self._pictures(ctx, top)))
        except Exception as exc:        # noqa: BLE001 - see the docstring
            warn(f"could not render report.html — {exc}")
            return None
        return page

    def _pictures(self, ctx: Context, top: str) -> list:
        """Step 8's renders, linked relative so the page works off the disk."""
        render_dir = ctx.ws.step("8_render")
        wanted = [(f"{top}.chip.png", "The hardened die, every layer, with the "
                                      "AION cells ringed"),
                  (f"{top}.aion.png", "Where the AION cells landed")]
        found = []
        for name, caption in wanted:
            if (render_dir / name).is_file():
                found.append((caption, f"../{render_dir.name}/{name}"))
        return found

    # -----------------------------------------------------------------
    def _ensure_baseline(self, ctx: Context) -> None:
        """Harden the PDK-only control, unless one is already on disk."""
        metrics = ctx.ws.baseline_dir / "metrics.json"
        if metrics.is_file() and not ctx.cfg.REPORT_REBUILD_BASELINE:
            info(f"baseline: reusing {workspace.rel(ctx.ws.baseline_dir)}")
            return
        if self.dry_run:
            info("would harden the PDK-only baseline")
            return

        if not ctx.cfg.REPORT_RUN_BASELINE:
            raise StepFailed(
                f"no PDK-only baseline at {workspace.rel(metrics)}, and "
                "REPORT_RUN_BASELINE is off\n"
                f"  run `make baseline DESIGN={ctx.ws.design.name}` yourself, or "
                "re-run with SET=REPORT_RUN_BASELINE=true", 2)
        if not container_running():
            raise StepFailed(
                f"no PDK-only baseline at {workspace.rel(metrics)}, and the EDA "
                "container is not running to build one", 4)

        warn("no PDK-only baseline on disk — hardening one now (`make baseline`)")
        build_baseline(ctx)
        self.require(metrics)
        ctx.note("hardened the PDK-only baseline")

    # -----------------------------------------------------------------
    def _report(self, ctx: Context, record: dict, page=None) -> None:
        if record["comparable"]:
            warn("the two runs are NOT configured the same way — every delta "
                 "below mixes the substitution with a config change:")
            for problem in record["comparable"]:
                note(f"  {problem}")

        info(record["headline"] and _verdict_line(record))
        print()
        _table(record["headline"]["rows"])
        print()

        totals = record.get("ledger_totals")
        if totals and totals.get("actual_saving") is not None:
            info("predicted vs delivered, on the substituted sites only")
            note(f"  predicted {totals['predicted_saving']:>9,.1f} um²  "
                 f"({totals['predicted_saving_pct']:.1f}% of the area replaced)")
            note(f"  delivered {totals['actual_saving']:>9,.1f} um²  "
                 f"({totals['actual_saving_pct']:.1f}%)")
            if totals["predicted_saving"] > 0:
                ratio = totals["actual_saving"] / totals["predicted_saving"]
                (ok if ratio >= 0.95 else warn)(
                    f"the drawn cells delivered {100 * ratio:.0f}% of what "
                    f"AREA_FACTOR={totals.get('area_factor')} promised")
        elif totals:
            warn("no AION cell has been published, so there is nothing to "
                 "compare against the prediction")

        names = "report.md, report.json" + (", report.html" if page else "")
        ok(f"{workspace.rel(self.outdir(ctx))}/ — {names}")
        head = record["headline"]
        ctx.note(f"vs baseline: {head['wins']} win, {head['losses']} loss, "
                 f"{head['ties']} tie")


def _verdict_line(record: dict) -> str:
    head = record["headline"]
    return (f"{head['wins']} win / {head['losses']} loss / {head['ties']} tie "
            f"across area, setup slack, power and wirelength")


def _table(rows: list) -> None:
    """The headline comparison, aligned, one line per metric."""
    print(color(f"    {'metric':<26}{'AION':>14}{'baseline':>14}"
                f"{'delta':>16}   ", Style.BOLD))
    for row in rows:
        digits = 0 if row["unit"] in ("", "um") else 3
        unit = f" {row['unit']}" if row["unit"] else ""
        delta = ("—" if row["absolute"] is None
                 else f"{row['absolute']:+,.{digits}f}"
                 + ("" if row["percent"] is None else f" ({row['percent']:+.2f}%)"))
        line = (f"    {row['label'] + unit:<26}"
                f"{_cell(row['aion'], digits):>14}"
                f"{_cell(row['baseline'], digits):>14}"
                f"{delta:>16}   ")
        print(line + color(row["verdict"], *VERDICT_STYLE[row["verdict"]]))


def _cell(value, digits: int) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


STEP = ReportStep()


def build_baseline(ctx) -> None:
    """RTL -> GDS with the PDK cells only, into <run>/baseline/."""
    librelane.prepare(ctx.ws, ctx.cfg, "baseline")
    librelane.run(ctx.ws, ctx.runner, "baseline", ctx.ws.baseline_dir,
                  name="librelane.baseline")
