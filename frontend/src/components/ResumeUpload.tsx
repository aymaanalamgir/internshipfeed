import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import type { ResumeMetadata } from '../types/profile';
import { ApiError } from '../api/http';
import { getLatestResume, uploadResume } from '../api/resumes';
import { validatePdf } from '../lib/form';

function uploadError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 413) return 'Choose a PDF of 5 MiB or less.';
    if (error.status === 415) return 'Only PDF files are supported.';
    if (error.detail === 'Could not extract readable text from this PDF.') return 'No readable text found. Choose a text-based PDF.';
    if (error.detail === 'PDF must contain at most 10 pages.') return 'Choose a PDF with 10 pages or fewer.';
    if (error.status === 422) return 'Could not read this PDF. Try a valid, text-based PDF.';
  }
  return 'PDF upload failed. Please try again.';
}

export function ResumeUpload() {
  const input = useRef<HTMLInputElement>(null);
  const [uploaded, setUploaded] = useState<ResumeMetadata | null>(null);
  const [selectedName, setSelectedName] = useState('');
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const uploadVersion = useRef(0);
  useEffect(() => {
    const controller = new AbortController();
    const version = uploadVersion.current;
    setLoadError(false);
    getLatestResume(controller.signal).then(value => { if (!controller.signal.aborted && version === uploadVersion.current) setUploaded(value); })
      .catch(() => { if (!controller.signal.aborted && version === uploadVersion.current) setLoadError(true); });
    return () => controller.abort();
  }, [attempt]);

  async function selected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ''; // Selecting the same PDF again should still trigger change.
    if (!file) return;
    setError('');
    setSelectedName(file.name);
    const validation = validatePdf(file);
    if (validation) { setError(validation); return; }
    uploadVersion.current += 1;
    setUploading(true);
    try {
      const stored = await uploadResume(file);
      setUploaded({ id: stored.id, candidate_profile_id: stored.candidate_profile_id, filename: stored.filename, uploaded_at: stored.uploaded_at });
      setLoadError(false);
    }
    catch (failure) { setError(uploadError(failure)); }
    finally { setUploading(false); }
  }

  return (
    <section className="resume-section" aria-labelledby="resume-label" aria-busy={uploading}>
      <div className="section-heading">
        <h3 id="resume-label">Résumé</h3>
        <span>PDF only · 5 MiB · 10 pages</span>
      </div>
      <div className="resume-row">
        <div className="resume-copy">
          {(uploaded || selectedName) && <p className="filename">{uploading ? selectedName : uploaded?.filename || selectedName}</p>}
          <p className={error ? 'field-error' : uploaded && !uploading ? 'saved' : 'muted'}
            id="upload-status" role={error ? 'alert' : 'status'}>
            {error || (uploading ? 'Uploading…' : uploaded ? `Uploaded ${new Intl.DateTimeFormat('en', { dateStyle: 'medium' }).format(new Date(uploaded.uploaded_at))}` : '')}
          </p>
        </div>
        <input ref={input} id="resume-file" type="file" accept=".pdf,application/pdf" className="sr-only"
          tabIndex={-1} disabled={uploading} onChange={selected} aria-label="Select résumé PDF" aria-describedby="upload-status" />
        <button type="button" className="button button-secondary" disabled={uploading} onClick={() => input.current?.click()}
          aria-describedby="upload-status">
          {uploading ? 'Uploading…' : uploaded ? 'Replace PDF' : 'Upload PDF'}
        </button>
      </div>
      {loadError && <p className="field-error">Could not load résumé. <button type="button" className="text-button" onClick={() => setAttempt(value => value + 1)}>Try again</button></p>}
    </section>
  );
}
