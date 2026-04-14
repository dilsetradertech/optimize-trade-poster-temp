import boto3, uuid
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import File, UploadFile, HTTPException, APIRouter, FastAPI
from dotenv import load_dotenv
from models.createdb import get_db_connection
import os, psycopg2

# Load environment variables
load_dotenv()

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

app = FastAPI(title="S3 Image Upload")
router = APIRouter()

# Initialize S3 client once
s3_client = boto3.client(
    "s3",
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
)

def upload_image_to_s3(file: UploadFile, username: str) -> str:
    try:
        if not file.filename or '.' not in file.filename:
            raise ValueError("Invalid file name.")

        file_extension = file.filename.split('.')[-1].lower()
        unique_filename = f"{username}_{uuid.uuid4()}.{file_extension}"

        s3_client.upload_fileobj(
            file.file,
            S3_BUCKET_NAME,
            unique_filename,
            ExtraArgs={"ContentType": file.content_type}
        )

        url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{unique_filename}"
        return url

    except (BotoCoreError, ClientError, ValueError) as e:
        raise Exception(f"Failed to upload to S3: {str(e)}")


# Upload Image
@router.post("/upload-image/{user_id}")
async def upload_image(user_id: str, file: UploadFile = File(...)):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT firstName FROM profiles WHERE user_id = %s", (user_id,))
        result = cur.fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="User not found")

        first_name = result[0].lower().replace(" ", "_")
        url = upload_image_to_s3(file, first_name)

        cur.execute("UPDATE profiles SET profileImage = %s WHERE user_id = %s", (url, user_id))
        conn.commit()

        return {"url": url}

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cur.close()
        conn.close()

# Include Router
app.include_router(router)