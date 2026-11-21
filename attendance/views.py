import csv
import datetime

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Attendance, Student

LOW_LIMIT = 75


def student_stats():
    students = list(
        Student.objects.annotate(
            total=Count("attendance"),
            present=Count("attendance", filter=Q(attendance__status="Present")),
        ).order_by("name")
    )
    for s in students:
        s.percent = round(s.present * 100 / s.total) if s.total else 0
    return students


# ---------- LOGIN and LOGOUT ----------

def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        user = authenticate(request,
                            username=request.POST.get("username", "").strip(),
                            password=request.POST.get("password", ""))
        if user is not None:
            login(request, user)
            return redirect("dashboard")
        messages.error(request, "Invalid username or password.")
    return render(request, "login.html")


def logout_view(request):
    if request.method == "POST":
        logout(request)
        messages.success(request, "You have been logged out.")
    return redirect("login")


# ---------- PAGES (login required) ----------

@login_required
def dashboard(request):
    today = timezone.localdate()
    start = today - datetime.timedelta(days=6)

    daily = {
        row["date"]: row
        for row in Attendance.objects.filter(date__range=(start, today))
        .values("date")
        .annotate(total=Count("id"), present=Count("id", filter=Q(status="Present")))
    }

    week = []
    for i in range(7):
        day = start + datetime.timedelta(days=i)
        row = daily.get(day)
        percent = round(row["present"] * 100 / row["total"]) if row else 0
        week.append({"label": day.strftime("%a"), "percent": percent})

    today_row = daily.get(today, {"total": 0, "present": 0})
    present = today_row["present"]
    absent = today_row["total"] - present
    percent = round(present * 100 / today_row["total"]) if today_row["total"] else 0

    low = [s for s in student_stats() if s.total and s.percent < LOW_LIMIT]
    low.sort(key=lambda s: s.percent)

    return render(request, "dashboard.html", {
        "total": Student.objects.count(),
        "present": present,
        "absent": absent,
        "percent": percent,
        "week": week,
        "low": low,
        "recent": Attendance.objects.select_related("student").order_by("-date", "-id")[:6],
    })


def _save_student(request, student=None):
    data = request.POST
    phone = data.get("phone", "").strip()

    if phone and (not phone.isdigit() or len(phone) != 10):
        messages.error(request, "Phone number must be exactly 10 digits.")
        return False

    try:
        if student is None:
            student = Student()
        student.roll_no = data["roll_no"].strip().upper()
        student.name = data["name"].strip()
        student.email = data["email"].strip()
        student.course = data["course"].strip()
        student.phone = phone
        student.save()
        return True
    except IntegrityError:
        messages.error(request, "Roll number or email already exists.")
        return False


@login_required
def add_student(request):
    if request.method == "POST":
        if _save_student(request):
            messages.success(request, "Student added successfully.")
            return redirect("view_students")
        return render(request, "add_student.html",
                      {"heading": "Add student", "f": request.POST})
    return render(request, "add_student.html", {"heading": "Add student", "f": {}})


@login_required
def edit_student(request, pk):
    student = get_object_or_404(Student, pk=pk)
    if request.method == "POST":
        if _save_student(request, student):
            messages.success(request, "Student updated.")
            return redirect("view_students")
        return render(request, "add_student.html",
                      {"heading": "Edit student", "f": request.POST})
    return render(request, "add_student.html",
                  {"heading": "Edit student", "f": student})


@login_required
@require_POST
def delete_student(request, pk):
    get_object_or_404(Student, pk=pk).delete()
    messages.success(request, "Student deleted.")
    return redirect("view_students")


@login_required
def view_students(request):
    return render(request, "view_students.html", {"students": student_stats()})


@login_required
def mark_attendance(request):
    today = timezone.localdate()

    if request.method == "POST":
        date = request.POST["date"]
        for s in Student.objects.all():
            Attendance.objects.update_or_create(
                student=s,
                date=date,
                defaults={"status": request.POST.get("status_" + str(s.id), "Absent")},
            )
        messages.success(request, "Attendance saved for " + date + ".")
        return redirect(reverse("reports") + "?date_from=" + date + "&date_to=" + date)

    try:
        date = datetime.date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        date = today

    existing = {a.student_id: a.status for a in Attendance.objects.filter(date=date)}
    students = list(Student.objects.order_by("roll_no"))
    for s in students:
        s.current = existing.get(s.id, "Present")

    return render(request, "mark_attendance.html", {
        "students": students,
        "date": date.isoformat(),
        "today": today.isoformat(),
        "already": bool(existing),
    })


@login_required
def reports(request):
    records = Attendance.objects.select_related("student").order_by("-date", "student__name")
    sid = request.GET.get("student", "")
    status = request.GET.get("status", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")

    if sid:
        records = records.filter(student_id=sid)
    if status:
        records = records.filter(status=status)
    if date_from:
        records = records.filter(date__gte=date_from)
    if date_to:
        records = records.filter(date__lte=date_to)

    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="attendance_report.csv"'
        writer = csv.writer(response)
        writer.writerow(["Date", "Roll no", "Name", "Course", "Status"])
        for r in records:
            writer.writerow([r.date, r.student.roll_no, r.student.name,
                             r.student.course, r.status])
        return response

    total = records.count()
    present = records.filter(status="Present").count()

    return render(request, "reports.html", {
        "records": records,
        "total": total,
        "present": present,
        "absent": total - present,
        "rate": round(present * 100 / total) if total else 0,
        "students": Student.objects.order_by("name"),
        "summary": student_stats(),
        "f": {"student": sid, "status": status,
              "date_from": date_from, "date_to": date_to},
        "qs": request.GET.urlencode(),
    })