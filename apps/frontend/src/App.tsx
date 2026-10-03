import { useEffect, useState } from 'react';

import { SidePanel } from './components/SidePanel';
import { StudyBrowser } from './components/StudyBrowser';
import { Toolbar } from './components/Toolbar';
import { TopBar } from './components/TopBar';
import { ViewportGrid } from './components/ViewportGrid';
import { api } from './lib/api/client';
import type { SmartContext } from './lib/api/types';

const UID_PATTERN = /^[0-9]+(\.[0-9]+)*$/;

/** Launch parameters, read once from the URL the app was opened with. */
function readLaunchParams() {
  const params = new URLSearchParams(window.location.search);
  const study = params.get('study');
  return {
    // `?study=<uid>`: deep link from a CDS Hooks card.
    studyUid: study && UID_PATTERN.test(study) ? study : null,
    // `?smart=1`: we have just returned from a SMART on FHIR launch.
    smartLaunch: params.get('smart') === '1',
  };
}

export default function App() {
  const [launch] = useState(readLaunchParams);
  const [smart, setSmart] = useState<SmartContext | null>(null);
  const [smartPending, setSmartPending] = useState(launch.smartLaunch);

  useEffect(() => {
    if (!launch.smartLaunch) return;
    api
      .smartContext()
      .then(setSmart)
      .catch(() => setSmart(null))
      .finally(() => setSmartPending(false));
    // Drop the marker so a reload does not look like a fresh launch.
    window.history.replaceState(null, '', window.location.pathname);
  }, [launch.smartLaunch]);

  return (
    <div className="flex h-full flex-col">
      <TopBar smart={smart} onSmartEnded={() => setSmart(null)} />
      <div className="flex min-h-0 flex-1">
        {/* Wait for the EHR context so the browser opens on the right patient. */}
        {!smartPending && <StudyBrowser initialStudyUid={launch.studyUid} smart={smart} />}
        <main className="flex min-w-0 flex-1 flex-col">
          <Toolbar />
          <ViewportGrid />
        </main>
        <SidePanel />
      </div>
    </div>
  );
}
