import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { sources } from "../test/fixtures";
import { FilterBar } from "./FilterBar";

const defaultProps = {
  searchValue: "",
  onSearchChange: vi.fn(),
  source: "",
  onSourceChange: vi.fn(),
  sources,
  sort: "newest" as const,
  onSortChange: vi.fn(),
  hasFilters: false,
  onClear: vi.fn(),
  total: 42,
  loading: false,
};

describe("FilterBar", () => {
  it("reports search input changes", async () => {
    const user = userEvent.setup();
    const onSearchChange = vi.fn();

    function ControlledFilterBar() {
      const [value, setValue] = useState("");
      return (
        <FilterBar
          {...defaultProps}
          searchValue={value}
          onSearchChange={(next) => {
            setValue(next);
            onSearchChange(next);
          }}
        />
      );
    }

    render(<ControlledFilterBar />);

    await user.type(screen.getByRole("searchbox"), "energy");

    expect(onSearchChange).toHaveBeenCalled();
    expect(onSearchChange.mock.calls.at(-1)?.[0]).toBe("energy");
  });

  it("lists only enabled sources and reports selection", async () => {
    const user = userEvent.setup();
    const onSourceChange = vi.fn();
    render(<FilterBar {...defaultProps} onSourceChange={onSourceChange} />);

    const select = screen.getByRole("combobox", { name: /Filter by source/i });
    expect(screen.getByRole("option", { name: "Example Wire" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Disabled Source" })).not.toBeInTheDocument();

    await user.selectOptions(select, "example");
    expect(onSourceChange).toHaveBeenCalledWith("example");
  });

  it("shows the clear action only when filters are active", async () => {
    const user = userEvent.setup();
    const onClear = vi.fn();
    const { rerender } = render(<FilterBar {...defaultProps} />);
    expect(screen.queryByRole("button", { name: /Clear filters/i })).not.toBeInTheDocument();

    rerender(<FilterBar {...defaultProps} hasFilters onClear={onClear} />);
    await user.click(screen.getByRole("button", { name: /Clear filters/i }));
    expect(onClear).toHaveBeenCalled();
  });
});
