import os
import boto3


AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"

textract = boto3.client("textract", region_name=AWS_REGION)


def extract_text_from_s3_image(bucket: str, key: str) -> dict:
    result = textract.detect_document_text(
        Document={
            "S3Object": {
                "Bucket": bucket,
                "Name": key
            }
        }
    )

    lines = []
    words = []

    for block in result.get("Blocks", []):
        block_type = block.get("BlockType")

        if block_type == "LINE":
            text = block.get("Text", "")
            if text:
                lines.append(text)

        if block_type == "WORD":
            text = block.get("Text", "")
            if text:
                words.append(text)

    clean_text = "\n".join(lines).strip()

    return {
        "cleanText": clean_text,
        "lineCount": len(lines),
        "wordCount": len(words),
        "rawBlockCount": len(result.get("Blocks", []))
    }
