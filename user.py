from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from typing import List

from app.database import get_db, Base, engine
import app.models as models
import app.schemas as schemas
import app.security as security

app = FastAPI(title="Professional Qarz Daftar API", version="2.0")

# CORS sozlamalari (Frontend ulanishi uchun)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Ishlab chiqarishda buni frontend manzili bilan almashtiring
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Server yonganda jadvallarni avtomat yaratish (agar yo'q bo'lsa)
@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# --- AUTHENTICATION (KIRISH/CHIQISH) ---

@app.post("/register", status_code=status.HTTP_201_CREATED)
async def register(user_in: schemas.UserCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.User).where(models.User.username == user_in.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Bu username allaqachon ro'yxatdan o'tgan.")
    
    new_user = models.User(
        username=user_in.username,
        hashed_password=security.hash_password(user_in.password)
    )
    db.add(new_user)
    await db.commit()
    return {"message": "Muvaffaqiyatli ro'yxatdan o'tdingiz!"}

@app.post("/login", response_model=schemas.Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.User).where(models.User.username == form_data.username))
    user = result.scalar_one_or_none()
    
    if not user or not security.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Username yoki parol xato!")
    
    access_token = security.create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

# --- QARZLARNI BOSHQARISH (CRUD + Dashboard) ---

@app.post("/debts/", response_model=schemas.DebtResponse)
async def create_debt(
    debt_in: schemas.DebtCreate, 
    current_user: models.User = Depends(security.get_current_user), 
    db: AsyncSession = Depends(get_db)
):
    new_debt = models.Debt(**debt_in.model_dump(), owner_id=current_user.id)
    db.add(new_debt)
    await db.commit()
    await db.refresh(new_debt)
    return new_debt

@app.get("/debts/dashboard", response_model=schemas.DebtDashboard)
async def get_dashboard_data(
    current_user: models.User = Depends(security.get_current_user), 
    db: AsyncSession = Depends(get_db)
):
    """Frontend Bosh sahifasi uchun barcha statistika va ro'yxatni bitta so'rovda qaytaradi"""
    # Foydalanuvchining hamma qarzlari
    result = await db.execute(select(models.Debt).where(models.Debt.owner_id == current_user.id).order_by(models.Debt.created_at.desc()))
    all_debts = result.scalars().all()

    # Odamlar bizdan qarzdor summa (is_paid = False va amount > 0)
    they_owe_res = await db.execute(
        select(func.sum(models.Debt.amount)).where(models.Debt.owner_id == current_user.id, models.Debt.is_paid == False, models.Debt.amount > 0)
    )
    # Biz odamlardan qarzdor summa (is_paid = False va amount < 0)
    we_owe_res = await db.execute(
        select(func.sum(models.Debt.amount)).where(models.Debt.owner_id == current_user.id, models.Debt.is_paid == False, models.Debt.amount < 0)
    )

    return {
        "total_they_owe": they_owe_res.scalar() or 0.0,
        "total_we_owe": abs(we_owe_res.scalar() or 0.0), # manfiy sonni musbat qilib ko'rsatish
        "debts": all_debts
    }

@app.patch("/debts/{debt_id}/toggle-paid", response_model=schemas.DebtResponse)
async def toggle_debt_status(
    debt_id: int, 
    current_user: models.User = Depends(security.get_current_user), 
    db: AsyncSession = Depends(get_db)
):
    """Qarzni uzilgan/uzilmagan holatga o'tkazish (Toggle)"""
    result = await db.execute(select(models.Debt).where(models.Debt.id == debt_id, models.Debt.owner_id == current_user.id))
    debt = result.scalar_one_or_none()
    if not debt:
        raise HTTPException(status_code=404, detail="Qarz topilmadi yoki sizga tegishli emas")
    
    debt.is_paid = not debt.is_paid
    await db.commit()
    await db.refresh(debt)
    return debt

@app.delete("/debts/{debt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_debt(
    debt_id: int, 
    current_user: models.User = Depends(security.get_current_user), 
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(models.Debt).where(models.Debt.id == debt_id, models.Debt.owner_id == current_user.id))
    debt = result.scalar_one_or_none()
    if not debt:
        raise HTTPException(status_code=404, detail="Qarz topilmadi")
    
    await db.delete(debt)
    await db.commit()
    return