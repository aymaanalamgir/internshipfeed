import { useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import type { ResumeMetadata } from '../types/profile';
import { ApiError } from '../api/http';
import { uploadResume } from '../api/resumes';
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

  async function selected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ''; // Selecting the same PDF again should still trigger change.
    if (!file) return;
    setError('');
    setSelectedName(file.name);
    const validation = validatePdf(file);
    if (validation) { setError(validation); return; }
    setUploading(true);
    try { setUploaded(await uploadResume(file)); }
    catch (failure) { setError(uploadError(failure)); }
    finally { setUploading(false); }
  }

  return (
    <section className="resume-section" aria-labelledby="resume-label" aria-busy={uploading}>
      <div className="section-heading">
        <h2 id="resume-label">Résumé</h2>
        <span>PDF only · 5 MiB · 10 pages</span>
      </div>
      <div className="resume-row">
        <svg className="file-icon" viewBox="0 0 32 38" fill="none" aria-hidden="true">
          <path d="M5 1h14l8 8v27H5V1Z" stroke="currentColor" strokeWidth="1.2" />
          <path d="M19 1v9h8M10 19h12M10 24h12M10 29h8" stroke="currentColor" strokeWidth="1.2" />
        </svg>
        <div className="resume-copy">
          <p className="filename">{selectedName || uploaded?.filename || 'No PDF uploaded this session'}</p>
          <p className={error ? 'field-error' : uploaded && !uploading ? 'saved' : 'muted'}
            id="upload-status" role={error ? 'alert' : 'status'}>
            {error || (uploading ? 'Uploading and reading PDF…' : uploaded ? 'Uploaded successfully' : 'Add a text-based résumé when you’re ready.')}
          </p>
        </div>
        <input ref={input} id="resume-file" type="file" accept=".pdf,application/pdf" className="sr-only"
          tabIndex={-1} disabled={uploading} onChange={selected} aria-label="Select résumé PDF" aria-describedby="upload-status" />
        <button type="button" className="button button-secondary" disabled={uploading} onClick={() => input.current?.click()}
          aria-describedby="upload-status">
          <span aria-hidden="true">↑</span>{uploading ? 'Uploading…' : uploaded ? 'Upload another PDF' : 'Upload PDF'}
        </button>
      </div>
      <p className="resume-note">Uploading a résumé won’t change your profile preferences.</p>
    </section>
  );
}
