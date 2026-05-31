/**
 * Vitest unit tests for the schema-import API client (P1b/P3).
 * Mocks global.fetch and asserts URL / method / Content-Type / body shape
 * and response parsing for importSchemaYaml + importSchemaDoc.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  importSchemaDoc,
  importSchemaYaml,
  type SchemaImportDoc,
  type SchemaImportResponse,
} from "@/lib/api";

const OK_RESPONSE: SchemaImportResponse = {
  imported: [
    {
      name: "Finance",
      schema_id: "abc",
      action: "created",
      current_version: 2,
      entities: 1,
      fields: 2,
      relationships: 0,
    },
  ],
};

function mockFetchOnce(body: unknown, ok = true, status = 200) {
  const fetchMock = vi.fn(async () => ({
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("importSchemaYaml", () => {
  it("POSTs raw YAML text with the x-yaml content type + workspace header", async () => {
    const fetchMock = mockFetchOnce(OK_RESPONSE);
    const yaml = "schemas:\n  - name: Finance\n";

    const res = await importSchemaYaml(yaml);

    expect(res).toEqual(OK_RESPONSE);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/schemas\/import\.yaml$/);
    expect(init.method).toBe("POST");
    expect(init.body).toBe(yaml);
    const headers = init.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/x-yaml");
    expect(headers["X-Test-Workspace"]).toBeDefined();
  });

  it("throws KbApiError on a non-2xx response", async () => {
    mockFetchOnce({ detail: "bad" }, false, 400);
    await expect(importSchemaYaml("nonsense")).rejects.toThrow();
  });
});

describe("importSchemaDoc", () => {
  it("POSTs the doc as JSON the server can yaml.safe_load", async () => {
    const fetchMock = mockFetchOnce(OK_RESPONSE);
    const doc: SchemaImportDoc = {
      schemas: [
        {
          name: "Finance",
          entities: [
            { name: "LoanAgreement", fields: [{ name: "principal", type: "number" }] },
          ],
        },
      ],
    };

    const res = await importSchemaDoc(doc);

    expect(res).toEqual(OK_RESPONSE);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/schemas\/import\.yaml$/);
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/json");
    // Body must be valid JSON round-tripping to the same doc.
    expect(JSON.parse(init.body as string)).toEqual(doc);
  });
});
