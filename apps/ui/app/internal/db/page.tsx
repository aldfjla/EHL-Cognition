/**
 * Internal database browser route.
 *
 * The interactive table picker and row editor live in
 * `components/internal/DatabaseBrowser`; this route only provides the page
 * frame and keeps the internal tool out of primary navigation.
 */

import DatabaseBrowser from "@/components/internal/DatabaseBrowser";

export default function InternalDatabasePage() {
  return (
    <main className="px-4 py-6 sm:px-6">
      <div className="mx-auto max-w-[1600px]">
        <DatabaseBrowser />
      </div>
    </main>
  );
}
