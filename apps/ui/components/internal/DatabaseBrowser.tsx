"use client";

/**
 * Dense, metadata-driven view of the local database.
 *
 * This is intentionally a maintenance surface rather than a second domain
 * model: the API supplies table and column metadata, and edits stay scoped to
 * existing rows.
 */

import clsx from "clsx";
import { useCallback, useEffect, useMemo, useState } from "react";

import * as api from "@/lib/api";
import type {
  InternalDbColumn,
  InternalDbRow,
  InternalDbTable,
} from "@/lib/types";

const PAGE_SIZE = 50;
const LONG_TEXT_COLUMNS = new Set(["body", "detail", "summary", "diff"]);

type EditState = {
  pk: string;
  column: InternalDbColumn;
  value: string;
  error: string | null;
};

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return String(value);
}

function primaryKeyValue(row: InternalDbRow, table: InternalDbTable): string {
  return displayValue(table.primary_key ? row[table.primary_key] : "");
}

function isTextarea(column: InternalDbColumn, value: unknown): boolean {
  return (
    column.name.endsWith("_json") ||
    LONG_TEXT_COLUMNS.has(column.name) ||
    displayValue(value).length > 120
  );
}

export default function DatabaseBrowser() {
  const [tables, setTables] = useState<InternalDbTable[]>([]);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [rows, setRows] = useState<InternalDbRow[]>([]);
  const [columns, setColumns] = useState<InternalDbColumn[]>([]);
  const [total, setTotal] = useState(0);
  const [pageRequest, setPageRequest] = useState({ offset: 0, revision: 0 });
  const [loadingTables, setLoadingTables] = useState(true);
  const [loadingRows, setLoadingRows] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [edit, setEdit] = useState<EditState | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const offset = pageRequest.offset;

  const selectedTable = useMemo(
    () => tables.find((table) => table.name === selectedName) ?? null,
    [selectedName, tables],
  );

  const loadRows = useCallback(
    async (tableName: string, pageOffset: number) => {
      setLoadingRows(true);
      setError(null);
      setEdit(null);
      try {
        const response = await api.listInternalDbRows(
          tableName,
          PAGE_SIZE,
          pageOffset,
        );
        setColumns(response.columns);
        setRows(response.rows);
        setTotal(response.total);
      } catch (err) {
        setError(err instanceof Error ? err.message : "database request failed");
      } finally {
        setLoadingRows(false);
      }
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;
    setLoadingTables(true);
    api
      .listInternalDbTables()
      .then((listed) => {
        if (cancelled) return;
        setTables(listed);
        setSelectedName((current) => current ?? listed[0]?.name ?? null);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "database request failed",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingTables(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (selectedName) void loadRows(selectedName, offset);
  }, [loadRows, offset, pageRequest.revision, selectedName]);

  const selectTable = (name: string) => {
    setSelectedName(name);
    setPageRequest((current) => ({ ...current, offset: 0 }));
  };

  const saveCell = async () => {
    if (!edit || !selectedTable) return;
    try {
      const updated = await api.updateInternalDbRow(
        selectedTable.name,
        edit.pk,
        { [edit.column.name]: edit.value },
      );
      setRows((current) =>
        current.map((row) =>
          primaryKeyValue(row, selectedTable) === edit.pk ? updated : row,
        ),
      );
      setEdit(null);
    } catch (err) {
      setEdit((current) =>
        current
          ? {
              ...current,
              error:
                err instanceof Error ? err.message : "database update failed",
            }
          : current,
      );
    }
  };

  const deleteRow = async (row: InternalDbRow) => {
    if (!selectedTable || !selectedTable.primary_key) return;
    const pk = primaryKeyValue(row, selectedTable);
    if (!window.confirm(`Delete ${selectedTable.name} row ${pk}?`)) return;
    setDeleting(pk);
    setError(null);
    try {
      await api.deleteInternalDbRow(selectedTable.name, pk);
      if (rows.length === 1 && offset > 0) {
        const remainingTotal = Math.max(0, total - 1);
        const lastOffset =
          Math.floor(Math.max(0, remainingTotal - 1) / PAGE_SIZE) * PAGE_SIZE;
        setPageRequest((current) => ({
          offset: Math.min(current.offset, lastOffset),
          revision: current.revision + 1,
        }));
      } else {
        setPageRequest((current) => ({
          ...current,
          revision: current.revision + 1,
        }));
      }
      setTables((current) =>
        current.map((table) =>
          table.name === selectedTable.name
            ? { ...table, row_count: Math.max(0, table.row_count - 1) }
            : table,
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "database delete failed");
    } finally {
      setDeleting(null);
    }
  };

  const first = total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + rows.length, total);
  const canPrevious = offset > 0;
  const canNext = offset + PAGE_SIZE < total;
  const gridColumns = {
    gridTemplateColumns: `repeat(${columns.length + 1}, minmax(150px, 1fr))`,
  };

  return (
    <section>
      <div className="mb-6">
        <p className="stub-label text-status-running">internal · database</p>
        <h1 className="mt-2 font-mono text-2xl font-semibold text-slate-100">
          Database browser
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">
          Inspect and maintain the rows already stored by Robot CI. New rows
          remain orchestrator-owned.
        </p>
      </div>

      {error && (
        <div className="mb-4 rounded border border-status-blocked/40 bg-status-blocked/10 px-3 py-2 text-sm text-status-blocked">
          <div>{error}</div>
          <div className="mt-1 text-xs text-slate-400">
            If the API is unavailable, start it with <code>make api</code>.
          </div>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
        <aside className="rounded border border-surface-border bg-surface-raised p-2">
          <div className="stub-label px-2 py-2">Tables</div>
          {loadingTables ? (
            <div className="px-2 py-3 font-mono text-xs text-slate-500">
              loading…
            </div>
          ) : tables.length === 0 ? (
            <div className="px-2 py-3 font-mono text-xs text-slate-500">
              no tables
            </div>
          ) : (
            <div className="space-y-0.5">
              {tables.map((table) => (
                <button
                  key={table.name}
                  type="button"
                  onClick={() => selectTable(table.name)}
                  className={clsx(
                    "flex w-full items-center justify-between rounded px-2 py-2 text-left font-mono text-xs",
                    selectedName === table.name
                      ? "bg-surface text-sky-300"
                      : "text-slate-400 hover:bg-surface hover:text-slate-200",
                  )}
                >
                  <span className="truncate">{table.name}</span>
                  <span className="ml-2 text-slate-600">{table.row_count}</span>
                </button>
              ))}
            </div>
          )}
        </aside>

        <div className="min-w-0 rounded border border-surface-border bg-surface-raised">
          {!selectedTable ? (
            <div className="p-6 font-mono text-sm text-slate-500">
              Select a table to inspect its rows.
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-surface-border px-4 py-3">
                <div>
                  <h2 className="font-mono text-sm text-slate-200">
                    {selectedTable.name}
                  </h2>
                  <p className="mt-1 font-mono text-[11px] text-slate-500">
                    {selectedTable.columns.length} columns ·{" "}
                    {selectedTable.primary_key
                      ? `primary key ${selectedTable.primary_key}`
                      : "read-only table"}
                  </p>
                </div>
                <div className="flex items-center gap-2 font-mono text-[11px] text-slate-500">
                  <button
                    type="button"
                    disabled={!canPrevious || loadingRows}
                    onClick={() =>
                      setPageRequest((current) => ({
                        ...current,
                        offset: Math.max(0, current.offset - PAGE_SIZE),
                      }))
                    }
                    className="rounded border border-surface-border px-2 py-1 enabled:hover:border-slate-500 enabled:hover:text-slate-300 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    Previous
                  </button>
                  <span>
                    showing {first}–{last} of {total}
                  </span>
                  <button
                    type="button"
                    disabled={!canNext || loadingRows}
                    onClick={() =>
                      setPageRequest((current) => ({
                        ...current,
                        offset: current.offset + PAGE_SIZE,
                      }))
                    }
                    className="rounded border border-surface-border px-2 py-1 enabled:hover:border-slate-500 enabled:hover:text-slate-300 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    Next
                  </button>
                </div>
              </div>

              <div className="overflow-x-auto">
                <div className="min-w-max">
                  <div
                    className="grid border-b border-surface-border bg-surface px-3 py-2 font-mono text-[11px] uppercase tracking-wider text-slate-500"
                    style={gridColumns}
                  >
                    {columns.map((column) => (
                      <div key={column.name} className="pr-3">
                        {column.name}
                      </div>
                    ))}
                    <div>actions</div>
                  </div>
                  {loadingRows ? (
                    <div className="px-3 py-6 font-mono text-xs text-slate-500">
                      loading…
                    </div>
                  ) : rows.length === 0 ? (
                    <div className="px-3 py-6 font-mono text-xs text-slate-500">
                      no stored rows
                    </div>
                  ) : (
                    rows.map((row) => {
                      const pk = primaryKeyValue(row, selectedTable);
                      const rowKey = selectedTable.primary_key
                        ? pk
                        : columns
                            .map((column) => displayValue(row[column.name]))
                            .join("\u001f");
                      return (
                        <div
                          key={rowKey}
                          className="grid border-b border-surface-border/70 font-mono text-xs text-slate-300 last:border-b-0"
                          style={gridColumns}
                        >
                          {columns.map((column) => {
                            const value = row[column.name];
                            const isEditing =
                              edit?.pk === pk && edit.column.name === column.name;
                            const readOnly =
                              column.primary_key || !selectedTable.primary_key;
                            return (
                              <div
                                key={column.name}
                                className={clsx(
                                  "min-w-0 border-r border-surface-border/50 px-3 py-2",
                                  !readOnly && "cursor-pointer hover:bg-surface",
                                )}
                                onClick={() => {
                                  if (!readOnly && !isEditing) {
                                    setEdit({
                                      pk,
                                      column,
                                      value:
                                        value === null ? "" : displayValue(value),
                                      error: null,
                                    });
                                  }
                                }}
                              >
                                {isEditing ? (
                                  <div className="space-y-1.5">
                                    {isTextarea(column, value) ? (
                                      <textarea
                                        autoFocus
                                        value={edit.value}
                                        rows={4}
                                        onChange={(event) =>
                                          setEdit((current) =>
                                            current
                                              ? {
                                                  ...current,
                                                  value: event.target.value,
                                                }
                                              : current,
                                          )
                                        }
                                        onKeyDown={(event) => {
                                          if (event.key === "Escape") setEdit(null);
                                          if (event.key === "Enter" && !event.shiftKey) {
                                            event.preventDefault();
                                            void saveCell();
                                          }
                                        }}
                                        className="w-full rounded border border-sky-500/60 bg-surface px-2 py-1 text-xs text-slate-100 outline-none"
                                      />
                                    ) : (
                                      <input
                                        autoFocus
                                        value={edit.value}
                                        onChange={(event) =>
                                          setEdit((current) =>
                                            current
                                              ? {
                                                  ...current,
                                                  value: event.target.value,
                                                }
                                              : current,
                                          )
                                        }
                                        onKeyDown={(event) => {
                                          if (event.key === "Escape") setEdit(null);
                                          if (event.key === "Enter") {
                                            event.preventDefault();
                                            void saveCell();
                                          }
                                        }}
                                        className="w-full rounded border border-sky-500/60 bg-surface px-2 py-1 text-xs text-slate-100 outline-none"
                                      />
                                    )}
                                    <div className="flex items-center gap-2">
                                      <button
                                        type="button"
                                        onClick={(event) => {
                                          event.stopPropagation();
                                          void saveCell();
                                        }}
                                        className="rounded bg-sky-500/20 px-2 py-1 text-[10px] text-sky-300 hover:bg-sky-500/30"
                                      >
                                        Save
                                      </button>
                                      <button
                                        type="button"
                                        onClick={(event) => {
                                          event.stopPropagation();
                                          setEdit(null);
                                        }}
                                        className="text-[10px] text-slate-500 hover:text-slate-300"
                                      >
                                        Cancel
                                      </button>
                                    </div>
                                    {edit.error && (
                                      <div className="break-words text-[10px] leading-4 text-status-blocked">
                                        {edit.error}
                                      </div>
                                    )}
                                  </div>
                                ) : (
                                  <span
                                    className="block max-w-[260px] truncate"
                                    title={displayValue(value)}
                                  >
                                    {displayValue(value)}
                                  </span>
                                )}
                              </div>
                            );
                          })}
                          <div className="px-3 py-2">
                            <button
                              type="button"
                              disabled={deleting === pk || !selectedTable.primary_key}
                              onClick={() => void deleteRow(row)}
                              className="rounded border border-status-blocked/40 px-2 py-1 text-[10px] text-status-blocked enabled:hover:bg-status-blocked/10 disabled:cursor-not-allowed disabled:opacity-40"
                            >
                              {deleting === pk ? "Deleting…" : "Delete"}
                            </button>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
