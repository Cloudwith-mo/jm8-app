# JournalM8 Backend

JournalM8 is a serverless journaling intelligence backend built on AWS. The goal is to turn handwritten or typed journal entries into structured, searchable, and analyzable self-reflection data.

This backend currently supports:

* Typed journal entries
* Image upload through S3 presigned URLs
* OCR extraction with Amazon Textract
* OCR transcript review/editing
* Mood, sentiment, and theme analysis
* DynamoDB-based journal timeline storage
* API Gateway HTTP API routes
* AWS CLI-first deployment from VS Code

## Current Architecture

```text
Client / curl / future frontend
   ↓
API Gateway HTTP API
   ↓
Python Lambda
   ↓
DynamoDB
   ↓
S3
   ↓
Textract
   ↓
CloudWatch Logs
```

## Current AWS Services

| Service              | Purpose                                       |
| -------------------- | --------------------------------------------- |
| API Gateway HTTP API | Public backend API routes                     |
| Lambda               | Python backend logic                          |
| DynamoDB             | Journal entries, OCR status, analysis results |
| S3                   | Raw uploaded journal images                   |
| Textract             | OCR extraction from uploaded journal images   |
| IAM                  | Lambda execution role and service permissions |
| CloudWatch           | Logs and execution visibility                 |

## Project Structure

```text
journalm8-backend/
├── bin/
│   ├── build
│   ├── create-api
│   ├── create-resources
│   ├── deploy
│   ├── invoke
│   ├── lint
│   ├── logs
│   └── package
├── events/
│   └── analyze-entry.json
├── function/
│   ├── app.py
│   ├── journal_analyzer.py
│   ├── ocr.py
│   ├── requirements.txt
│   └── storage.py
├── infra/
│   └── lambda-trust-policy.json
├── template.yaml
├── samconfig.toml
├── requirements-dev.txt
└── README.md
```

## Completed Phases

### Phase 1: Lambda ZIP Deployment

Goal: prove the backend deployment loop.

```text
Local Python code
→ ZIP package
→ AWS Lambda
→ AWS CLI invoke
→ CloudWatch logs
```

Completed:

* Created Python Lambda package
* Created IAM Lambda execution role
* Deployed Lambda through AWS CLI
* Invoked Lambda from terminal
* Verified execution in CloudWatch logs

### Phase 2: API Gateway + DynamoDB + S3

Goal: create the first real serverless backend.

Completed:

* Created DynamoDB table: `journalm8-dev-main`
* Created private S3 bucket for raw uploads
* Created API Gateway HTTP API
* Added Lambda proxy integration
* Added journal entry routes
* Added presigned S3 upload URL route
* Confirmed S3 upload and DynamoDB metadata

### Phase 3: OCR with Textract

Goal: extract text from uploaded journal images.

Completed:

* Added OCR route: `POST /entries/{entryId}/ocr`
* Added Textract `DetectDocumentText`
* Extracted text from S3 image uploads
* Saved `cleanText` to DynamoDB
* Added OCR metadata:

  * `ocrStatus`
  * `ocrCompletedAt`
  * `ocrWordCount`
  * `ocrLineCount`
  * `ocrRawBlockCount`

### Phase 4: OCR Review/Edit Endpoint

Goal: allow user-reviewed transcript correction before analysis.

Completed:

* Added route: `PUT /entries/{entryId}/review`
* Saved corrected `cleanText`
* Added `reviewStatus`
* Added `reviewedAt`
* Reset `analysisStatus` to `NOT_ANALYZED`
* Removed stale analysis when reviewed text changes
* Confirmed analysis runs on reviewed transcript

## Current API Routes

| Method | Route                        | Purpose                                   |
| ------ | ---------------------------- | ----------------------------------------- |
| `POST` | `/entries`                   | Create a typed journal entry              |
| `GET`  | `/entries`                   | List entries for a user                   |
| `GET`  | `/entries/{entryId}`         | Get one journal entry                     |
| `POST` | `/entries/{entryId}/analyze` | Analyze entry mood, sentiment, and themes |
| `POST` | `/entries/{entryId}/ocr`     | Run OCR on uploaded image entry           |
| `PUT`  | `/entries/{entryId}/review`  | Save corrected OCR transcript             |
| `POST` | `/upload-url`                | Generate S3 presigned upload URL          |

## DynamoDB Design

Main table:

```text
journalm8-dev-main
```

Primary key:

```text
PK = USER#<userId>
SK = ENTRY#<timestamp>#<entryId>
```

GSI:

```text
GSI1PK = ENTRY#<entryId>
GSI1SK = USER#<userId>
```

This supports the main JM8 access patterns:

* Get all entries for one user
* Get latest entries for one user
* Get one specific entry by entry ID
* Track image upload/OCR/review/analysis status

## Example Entry Lifecycle

```text
UPLOAD_URL_CREATED
   ↓
OCR_PROCESSING
   ↓
OCR_COMPLETED
   ↓
REVIEWED
   ↓
ANALYZED
```

## Local Setup

Create and activate virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install local development dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

Load environment variables:

```bash
source .env
```

Expected local environment variables:

```bash
AWS_PROFILE=jm8-dev
AWS_REGION=us-east-1
APP_NAME=journalm8
STAGE=dev
TABLE_NAME=journalm8-dev-main
RAW_BUCKET=journalm8-dev-raw-<account-id>
API_NAME=journalm8-dev-api
API_ENDPOINT=https://<api-id>.execute-api.us-east-1.amazonaws.com
```

Do not commit `.env`.

## Development Commands

Lint and import-check Lambda:

```bash
./bin/lint
```

Build package folder:

```bash
./bin/build
```

Create ZIP package:

```bash
./bin/package
```

Create/update AWS resources:

```bash
./bin/create-resources
```

Deploy Lambda:

```bash
./bin/deploy
```

Create/update API Gateway routes:

```bash
./bin/create-api
```

Tail Lambda logs:

```bash
./bin/logs
```

## Test Commands

Create typed entry:

```bash
curl -s -X POST "$API_ENDPOINT/entries" \
  -H "content-type: application/json" \
  -H "x-user-id: demo-user" \
  -d '{"text":"Today I felt focused and disciplined building JM8."}' | jq
```

List entries:

```bash
curl -s "$API_ENDPOINT/entries" \
  -H "x-user-id: demo-user" | jq
```

Get one entry:

```bash
curl -s "$API_ENDPOINT/entries/$ENTRY_ID" \
  -H "x-user-id: demo-user" | jq
```

Analyze entry:

```bash
curl -s -X POST "$API_ENDPOINT/entries/$ENTRY_ID/analyze" \
  -H "x-user-id: demo-user" | jq
```

Create upload URL:

```bash
curl -s -X POST "$API_ENDPOINT/upload-url" \
  -H "content-type: application/json" \
  -H "x-user-id: demo-user" \
  -d '{
    "fileName": "journal-page.jpg",
    "contentType": "image/jpeg"
  }' | jq
```

Run OCR:

```bash
curl -s -X POST "$API_ENDPOINT/entries/$IMAGE_ENTRY_ID/ocr" \
  -H "x-user-id: demo-user" | jq
```

Review OCR transcript:

```bash
jq -Rs '{cleanText: .}' review-text.txt > review-payload.json

curl -s -X PUT "$API_ENDPOINT/entries/$IMAGE_ENTRY_ID/review" \
  -H "content-type: application/json" \
  -H "x-user-id: demo-user" \
  --data-binary @review-payload.json | jq
```

Analyze reviewed transcript:

```bash
curl -s -X POST "$API_ENDPOINT/entries/$IMAGE_ENTRY_ID/analyze" \
  -H "x-user-id: demo-user" | jq
```

## Security Notes

* `.env` is ignored and should never be committed.
* `.venv/`, build folders, ZIP packages, and local OCR review files are ignored.
* S3 bucket public access is blocked.
* Lambda uses IAM permissions for DynamoDB, S3, and Textract.
* Current user isolation is simulated with `x-user-id`.
* Cognito authentication will be added in a later phase.

## Next Phase

### Phase 5: Frontend MVP

Goal: build a simple UI around the working backend.

Planned screens:

* Journal timeline
* Entry detail page
* Image upload page
* OCR processing result page
* OCR review/edit page
* Analysis result page

Target flow:

```text
User uploads journal image
→ S3 stores image
→ OCR extracts text
→ User reviews transcript
→ JM8 saves reviewed entry
→ User analyzes entry
→ UI displays mood, sentiment, and themes
```

## Long-Term Roadmap

Future phases:

* Cognito authentication
* User-specific private journal space
* Step Functions OCR workflow
* Async OCR jobs for PDFs/multipage uploads
* AI-powered deeper journal analysis
* Weekly/monthly reflection summaries
* Search by date, mood, theme, and keyword
* Semantic search and RAG over journal history
* Frontend app with React/Vite or Expo
* Payment/paywall layer
* Production hardening with alarms, WAF, KMS, budgets, and CI/CD
