from fastapi import FastAPI, HTTPException, Depends, Request, Cookie,File,Form,status, UploadFile
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, Response, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from project_models import User, Base, async_session, engine, Problem, AdminResponse, ServiceRecord,Users_in_telegram
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from functools import wraps
from flask import redirect, url_for, session, flash
from datetime import datetime, timedelta, date
from tg_bot import main, send_msg
import bcrypt
import jwt
import secrets
import string
import asyncio


SECRET_KEY = 'kW!8729ew95P$be5j532#8Qlv;3&5tJ3'
ALGORITHM = "HS256"

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

templates = Jinja2Templates(directory='templates')

async def get_session():
    async with async_session() as session:
        yield session

def get_current_user(access_token: str = Cookie(None)):
    if not access_token:
        raise HTTPException(status_code=401, detail="Неавторизовано")
    try:
        payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        role = payload.get("role")
        if user_id is None or role is None:
            raise HTTPException(status_code=401)
        return user_id, role
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Недійсний токен")

def admin_required(user_data: tuple = Depends(get_current_user)) -> bool:
    user_id, role = user_data
    if role != "admin":
        raise HTTPException(status_code=403, detail="Доступ лише для адміністраторів")
    return True

@app.exception_handler(Exception)
async def internal_server_error_handler(request: Request, exc: Exception):
    return templates.TemplateResponse(
        "500.html",
        {"request": request, "detail": "Упс! Щось пішло не так..."},
        status_code=500
    )

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("role") != "admin":
            flash("Доступ лише для адміністратора!", "warning")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated_function

############################################### Головна и тест

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

@app.get("/test500")
async def test_error():
    raise Exception("Тестова помилка для перевірки сторінки 500")

################################################################## реєстрація

def generate_code():
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(6))

@app.get("/register")
async def create_user1(request: Request):
    return templates.TemplateResponse('register.html',{'request':request})

@app.post("/register")
async def create_user2(request: Request,username:str=Form(), password:str=Form(),email:str=Form(), session: AsyncSession = Depends(get_session)):
    new_user = User(username = username, email= email)
    new_user.set_password(raw_password=password)
    session.add(new_user)
    await session.commit()
    await session.refresh(new_user)

    tg_code = generate_code()
    user_in_tg = Users_in_telegram(tg_code=tg_code,user_in_site=new_user.id)
    session.add(user_in_tg)
    await session.commit()

    return templates.TemplateResponse('register.html',{'request':request,'message':'Ви успішно створили акаунт!',"tg_message":f"Якщо ви хочете отримувати сповіщення про зміну статусу ваших заявок - переходьте до (посилання на бота)\\nТа надайте йому цей код: {tg_code}"})

################################################################### логін

@app.get("/login")
async def aut_user1(request: Request, error:str = None):
    return templates.TemplateResponse('login.html', {'request': request,'error':error})

@app.post("/login")
async def aut_user2(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session)):
    user_select = await session.execute( select(User).filter(User.username == form_data.username))
    user = user_select.scalars().first()

    if not user or not bcrypt.checkpw(form_data.password.encode(), user.password.encode()):
        return RedirectResponse(url="/login/?error=Пароль або логін невірний, спробуйте ще раз", status_code=302)

    token_data = {
        "user_id": user.id,
        "role": "admin" if user.is_admin else "user",
        "exp": datetime.utcnow() + timedelta(hours=24*3)
    }
    token = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)

    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=60*60*24*3,
        samesite="lax"
    )
    return response
    
    
@app.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"message": "Ви вийшли з системи"}

########################################################################### проблеми та відповіді

@app.get('/add_my_problem')
async def add_problem1(request: Request):
    return templates.TemplateResponse('add_problem.html', {'request': request})

@app.post('/add_my_problem')
async def add_problem2(request: Request, title:str=Form(), description:str=Form(), img = File(None), current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    img_path = None
    if img.filename:
        file_location = f"user_problem_image/{img.filename}"
        with open('static/'+ file_location, "wb+") as f:
            f.write(await img.read())
        img_path = file_location

    new_problem = Problem(
        title=title,
        description=description,
        user_id=current_user[0],
        image_url=img_path
    )
    session.add(new_problem)
    await session.commit()
    await session.refresh(new_problem)
    return templates.TemplateResponse('add_problem.html', {'request': request,'message':f'Проблема: "{title}" записана!'})

@app.get('/new_problems')
async def user_problems(request: Request,session: AsyncSession = Depends(get_session) , is_admin: int = Depends(admin_required)):
    new_problems = await session.execute(select(Problem.id, Problem.title, Problem.description, Problem.date_created).filter_by(status="В обробці"))
    new_problems = new_problems.all()
    print(new_problems)
    return templates.TemplateResponse('all_problems.html',{'request':request,'problems':new_problems})

@app.get('/my_all_problems')
async def my_all_problems(request: Request, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    all_problems = await session.execute(select(Problem).filter_by(user_id=current_user[0]))
    problems = all_problems.scalars().all()
    return templates.TemplateResponse('all_my_problems.html',{'request':request,'problems':problems})

@app.get('/problem')
async def user_problem(problem_id: int, request: Request, session: AsyncSession = Depends(get_session), is_admin: int = Depends(admin_required)):
    problem = await session.execute(select(Problem).filter_by(id = problem_id))
    problem = problem.scalars().first()
    return templates.TemplateResponse('problem_check.html', {'request': request,'problem':problem})

@app.post('/problem')
async def take_problem(request: Request, current_user: User = Depends(get_current_user),id:int=Form(), session: AsyncSession = Depends(get_session), is_admin: int = Depends(admin_required)):
    problem = await session.execute(select(Problem).filter_by(id=id))
    problem = problem.scalar_one_or_none()
    if problem:
        problem.status = 'У роботі'
        problem.admin_id = current_user[0]
        session.add(problem)
        await session.commit()
        await session.refresh(problem)
    return templates.TemplateResponse('problem_check.html', {'request': request, 'problem': problem, 'message':'Заявку взято в роботу!'})

@app.get('/check_message')
async def my_all_prblms(id :int, request: Request, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    problem = await session.execute(select(Problem).filter_by(id=id))
    problem = problem.scalars().one_or_none()
    problem_answer = await session.execute(select(AdminResponse).filter_by(problem_id=id))
    problem_answer = problem_answer.scalars().one_or_none()
    return templates.TemplateResponse('check_message.html',{'request':request, 'problem':problem, 'answer':problem_answer})

#################################################### Передивлятися проблеми

@app.get('/service_record_review')
async def service_record_review(
    id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session)
):
    problem_result = await session.execute(select(Problem).filter_by(id=id))
    problem = problem_result.scalars().one_or_none()

    if not problem:
        raise HTTPException(status_code=404, detail="Problem not found")

    record_result = await session.execute(select(ServiceRecord).filter_by(problem_id=id))
    service_record = record_result.scalars().one_or_none()

    return templates.TemplateResponse(
        'service_check.html',
        {
            'request': request,
            'problem': problem,
            'service_record': service_record
        }
    )
#################################################### Админка

@app.get('/admin_problems')
async def admin_problams(request: Request, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),is_admin: int = Depends(admin_required)):
    new_problems = await session.execute(select(Problem).filter_by(admin_id = current_user[0]))
    new_problems = new_problems.scalars().all()
    return templates.TemplateResponse('admin_problems.html',{'request': request, 'problems': new_problems})

def admin_required(user_data: tuple = Depends(get_current_user)) -> bool:
    user_id, role = user_data
    if role != "admin":
        raise HTTPException(status_code=403, detail="Доступ лише для адміністраторів")
    return True

#################################################
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(main())
    await init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)