# JM8 App

JM8 is a serverless journaling intelligence app that turns typed or handwritten journal entries into reviewed, analyzable self-reflection data.

## Structure

jm8-app/
├── backend/
└── frontend/

## Backend

Built with:

- AWS API Gateway
- AWS Lambda
- DynamoDB
- S3
- Textract
- IAM
- CloudWatch

Completed backend phases:

- Phase 1: Lambda ZIP deployment
- Phase 2: API Gateway + DynamoDB + S3
- Phase 3: Textract OCR
- Phase 4: OCR review/edit endpoint

## Frontend

Built with:

- React
- Vite
- TypeScript

Completed frontend MVP:

- Create typed entry
- List entries
- View entry detail
- Analyze typed entry
- Upload image to S3
- Run OCR
- Review/edit transcript
- Save review
- Analyze reviewed text

## Local Development

Backend:

cd backend
source .venv/bin/activate
source .env
./bin/lint
./bin/deploy

Frontend:

cd frontend
npm install
npm run dev

## Security Notes

Do not commit:

- .env
- .env.local
- AWS credentials
- presigned URLs
- journal images
- private journal text
