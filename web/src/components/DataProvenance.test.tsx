import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import DataProvenance from "./DataProvenance";
import { fmtNum } from "@/utils/format";

describe("contrato de proveniência", () => {
  it("não inventa xG nem timestamp ausente", () => {
    render(<DataProvenance updated="2025-01-01" data={{ source: "real", model_version: "BASELINE_V1", data_version: "v1", prediction_timestamp: "2025-01-01", odds_timestamp: null, calibration_status: "UNCALIBRATED", calibration_version: null, xg_status: "UNAVAILABLE", xg_source: null }} />);
    expect(screen.getByText(/Timestamp das odds: indisponível/)).toBeInTheDocument();
    expect(screen.getByText(/xG: UNAVAILABLE/)).toBeInTheDocument();
    expect(fmtNum(null)).toBe("—");
  });
});
