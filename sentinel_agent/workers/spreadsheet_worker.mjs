import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const payloadPath = process.argv[2];
if (!payloadPath) throw new Error("A JSON payload path is required");
const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));

let workbook;
let sheet;
if (payload.operation === "create") {
  workbook = Workbook.create();
  sheet = workbook.worksheets.add(payload.sheet_name || "Sheet1");
  const headers = payload.headers?.length ? payload.headers : ["Item", "Value"];
  const rows = payload.rows?.length ? payload.rows : [["Example", 1]];
  const startRow = payload.title ? 2 : 1;
  const lastColumn = String.fromCharCode(64 + Math.min(headers.length, 26));
  if (payload.title) {
    const titleRange = sheet.getRange(`A1:${lastColumn}1`);
    titleRange.merge();
    titleRange.values = [[payload.title]];
    titleRange.format = {
      fill: "#172554",
      font: { bold: true, color: "#FFFFFF", size: 18 },
      verticalAlignment: "center",
    };
    titleRange.format.rowHeight = 30;
  }
  const headerRange = sheet.getRange(`A${startRow}:${lastColumn}${startRow}`);
  headerRange.values = [headers];
  headerRange.format = {
    fill: "#0F766E",
    font: { bold: true, color: "#FFFFFF" },
    verticalAlignment: "center",
    wrapText: true,
  };
  const dataStart = startRow + 1;
  const dataEnd = dataStart + rows.length - 1;
  sheet.getRange(`A${dataStart}:${lastColumn}${dataEnd}`).values = rows;
  const grid = sheet.getRange(`A${startRow}:${lastColumn}${dataEnd}`);
  grid.format.borders = { preset: "all", style: "thin", color: "#D1D5DB" };
  grid.format.autofitColumns();
  grid.format.autofitRows();
  for (let column = 0; column < headers.length; column += 1) {
    const width = Math.min(
      38,
      Math.max(12, String(headers[column]).length + 2, ...rows.map(row => String(row[column] ?? "").length + 2)),
    );
    sheet.getRangeByIndexes(0, column, dataEnd, 1).format.columnWidth = width;
  }
  sheet.freezePanes.freezeRows(startRow);
} else if (payload.operation === "edit") {
  const bytes = await fs.readFile(payload.input_path);
  workbook = await SpreadsheetFile.importXlsx(new Uint8Array(bytes));
  sheet = workbook.worksheets.getOrAdd(payload.sheet_name || "Sheet1", {
    renameFirstIfOnlyNewSpreadsheet: true,
  });
  for (const update of payload.updates || []) {
    const range = sheet.getRange(update.cell);
    if (update.formula != null) range.formulas = [[update.formula]];
    else range.values = [[update.value]];
    if (update.number_format) range.format.numberFormat = update.number_format;
  }
  const used = sheet.getUsedRange();
  if (used) {
    used.format.autofitRows();
  }
} else {
  throw new Error(`Unsupported spreadsheet operation: ${payload.operation}`);
}

await fs.mkdir(payload.qa_dir, { recursive: true });
const preview = await workbook.render({
  sheetName: sheet.name,
  autoCrop: "all",
  scale: 1.5,
  format: "png",
});
await fs.writeFile(payload.preview_path, new Uint8Array(await preview.arrayBuffer()));
const inspection = await workbook.inspect({
  kind: "table,formula",
  sheetId: sheet.name,
  range: "A1:J20",
  maxChars: 3000,
  tableMaxRows: 20,
  tableMaxCols: 10,
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(payload.output_path);
console.log("SENTINEL_RESULT=" + JSON.stringify({
  output_path: payload.output_path,
  preview_path: payload.preview_path,
  inspection: inspection.ndjson,
  formula_errors: errors.ndjson,
}));
