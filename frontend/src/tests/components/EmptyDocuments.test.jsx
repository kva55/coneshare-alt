import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { EmptyDocuments } from "../../components/documents/EmptyDocuments";
import i18n from "../../i18n";

describe("EmptyDocuments", () => {
  it("renders the main heading and instructional text", () => {
    i18n.changeLanguage('en');
    render(<EmptyDocuments />);
    expect(
      screen.getByRole("heading", { name: "No documents yet" })
    ).toBeInTheDocument();
    expect(
      screen.getByText("Drag and drop files or folders here, or use the upload button to get started.")
    ).toBeInTheDocument();
  });

});
