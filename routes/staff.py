from flask import Blueprint, render_template, redirect, url_for, session, flash, request, current_app, send_file, jsonify   
from functools import wraps
from datetime import datetime, date, time, timedelta
from werkzeug.utils import secure_filename

from models.models import db, User, Booking, Room, UserProfile, BookingStatus, Sanction, SanctionStatus

staff_bp = Blueprint('staff', __name__, url_prefix='/staff')

def staff_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        
        user = User.query.get(session['user_id'])
        if not user or user.role_id != 2:  # role_id 2 = staff
            flash('Akses ditolak. Hanya staff yang dapat mengakses halaman ini.', 'error')
            return redirect(url_for('login'))
        
        return f(*args, **kwargs)
    return decorated_function

@staff_bp.route('/dashboard')
@staff_required
def staff_dashboard():
    # Ambil semua booking yang perlu dikelola (pending, approved, completed, cancelled)
    recent_bookings = Booking.query\
        .join(User, Booking.user_id == User.id)\
        .join(Room, Booking.room_id == Room.id)\
        .add_columns(
            User.username,
            User.email,
            Room.name.label('room_name')
        )\
        .order_by(Booking.created_at.desc())\
        .limit(20).all()  # Tingkatkan limit menjadi 20
    
    # Kirim fungsi today ke template untuk menghitung perbedaan hari
    return render_template('staff/dashboard.html', 
                         recent_bookings=recent_bookings,
                         today=date.today)


@staff_bp.route('/approve-booking/<int:booking_id>', methods=['POST'])
@staff_required
def approve_booking(booking_id):
    """Route untuk menyetujui booking"""
    try:
        booking = Booking.query.get_or_404(booking_id)
        
        # Cek apakah booking masih dalam status pending
        if booking.status != BookingStatus.PENDING:
            return jsonify({
                'success': False,
                'message': 'Booking sudah diproses sebelumnya'
            }), 400
        
        # Cek apakah ada konflik waktu dengan booking yang sudah disetujui
        conflicting_booking = Booking.query.filter(
            Booking.room_id == booking.room_id,
            Booking.booking_date == booking.booking_date,
            Booking.status == BookingStatus.APPROVED,
            Booking.id != booking_id,
            db.or_(
                db.and_(
                    Booking.start_time <= booking.start_time,
                    Booking.end_time > booking.start_time
                ),
                db.and_(
                    Booking.start_time < booking.end_time,
                    Booking.end_time >= booking.end_time
                ),
                db.and_(
                    Booking.start_time >= booking.start_time,
                    Booking.end_time <= booking.end_time
                )
            )
        ).first()
        
        if conflicting_booking:
            return jsonify({
                'success': False,
                'message': 'Ada booking lain yang sudah disetujui pada waktu yang sama'
            }), 400
        
        # Update status booking
        booking.status = BookingStatus.APPROVED
        booking.approved_by = session['user_id']
        booking.approved_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Booking berhasil disetujui'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'Terjadi kesalahan: {str(e)}'
        }), 500

@staff_bp.route('/reject-booking/<int:booking_id>', methods=['POST'])
@staff_required
def reject_booking(booking_id):
    """Route untuk menolak booking"""
    try:
        booking = Booking.query.get_or_404(booking_id)
        
        # Cek apakah booking masih dalam status pending
        if booking.status != BookingStatus.PENDING.value:
            return jsonify({
                'success': False,
                'message': 'Booking sudah diproses sebelumnya'
            }), 400
        
        # Ambil alasan penolakan dari request
        data = request.get_json()
        reason = data.get('reason', '') if data else ''
        
        if not reason or reason.strip() == '':
            return jsonify({
                'success': False,
                'message': 'Alasan penolakan harus diisi'
            }), 400
        
        # Update status booking
        booking.status = BookingStatus.REJECTED.value
        booking.approved_by = session['user_id']
        booking.approved_at = datetime.utcnow()
        
        # Simpan alasan penolakan di purpose (atau buat field rejection_reason jika perlu)
        if booking.purpose:
            booking.purpose += f"\n\n[DITOLAK] Alasan: {reason}"
        else:
            booking.purpose = f"[DITOLAK] Alasan: {reason}"
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Booking berhasil ditolak'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'Terjadi kesalahan: {str(e)}'
        }), 500

@staff_bp.route('/cancel-booking/<int:booking_id>', methods=['POST'])
@staff_required
def cancel_booking(booking_id):
    """Route untuk membatalkan booking yang sudah disetujui"""
    try:
        booking = Booking.query.get_or_404(booking_id)
        
        # Cek apakah booking dalam status approved
        if booking.status != BookingStatus.APPROVED.value:
            return jsonify({
                'success': False,
                'message': 'Hanya booking yang sudah disetujui yang dapat dibatalkan'
            }), 400
        
        # Cek apakah booking sudah dimulai (opsional)
        current_datetime = datetime.now()
        booking_datetime = datetime.combine(booking.booking_date, booking.start_time)
        
        if current_datetime >= booking_datetime:
            return jsonify({
                'success': False,
                'message': 'Tidak dapat membatalkan booking yang sudah dimulai'
            }), 400
        
        # Update status booking
        booking.status = BookingStatus.REJECTED.value
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Booking berhasil dibatalkan'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'Terjadi kesalahan: {str(e)}'
        }), 500
    
@staff_bp.route('/booking/<int:booking_id>')
@staff_required
def booking_detail(booking_id):
    """Route untuk melihat detail booking"""
    booking = Booking.query.filter_by(id=booking_id)\
        .join(User, Booking.user_id == User.id)\
        .join(Room, Booking.room_id == Room.id)\
        .add_columns(
            User.username,
            User.email,
            Room.name.label('room_name'),
            Room.description.label('room_description'),
            Room.capacity.label('room_capacity')
        ).first_or_404()
    
    # Ambil data approver jika ada
    approver = None
    if booking.Booking.approved_by:
        approver = User.query.get(booking.Booking.approved_by)
    
    # Ambil profil user jika ada
    user_profile = UserProfile.query.filter_by(user_id=booking.Booking.user_id).first()
    
    # Ambil data sanksi jika ada
    sanctions = Sanction.query.filter_by(booking_id=booking_id).all()
    
    return render_template('staff/booking_detail.html',
                         booking=booking,
                         approver=approver,
                         user_profile=user_profile,
                         sanctions=sanctions)

@staff_bp.route('/bookings')
@staff_required
def staff_bookings():
    """Route untuk melihat semua booking dengan filter"""
    # Ambil parameter filter dari query string
    status_filter = request.args.get('status', 'all')
    date_filter = request.args.get('date', 'all')
    room_filter = request.args.get('room', 'all')
    
    # Base query
    query = Booking.query.join(User, Booking.user_id == User.id)\
                        .join(Room, Booking.room_id == Room.id)\
                        .add_columns(
                            User.username,
                            User.email,
                            Room.name.label('room_name')
                        )
    
    # Apply filters
    if status_filter != 'all':
        if status_filter == 'pending':
            query = query.filter(Booking.status == BookingStatus.PENDING.value)
        elif status_filter == 'approved':
            query = query.filter(Booking.status == BookingStatus.APPROVED.value)
        elif status_filter == 'completed':
            query = query.filter(Booking.status == BookingStatus.COMPLETED.value)
        elif status_filter == 'cancelled':
            query = query.filter(Booking.status == BookingStatus.REJECTED.value)
    
    if date_filter == 'today':
        query = query.filter(Booking.booking_date == date.today())
    elif date_filter == 'tomorrow':
        query = query.filter(Booking.booking_date == date.today() + timedelta(days=1))
    elif date_filter == 'this_week':
        today = date.today()
        start_week = today - timedelta(days=today.weekday())
        end_week = start_week + timedelta(days=6)
        query = query.filter(Booking.booking_date.between(start_week, end_week))
    
    if room_filter != 'all':
        query = query.filter(Room.id == int(room_filter))
    
    # Order by created_at desc
    bookings = query.order_by(Booking.created_at.desc()).all()
    
    # Ambil data ruangan untuk filter dropdown
    rooms = Room.query.filter_by(is_active=True).all()
    
    return render_template('staff/bookings.html',
                         bookings=bookings,
                         rooms=rooms,
                         current_filters={
                             'status': status_filter,
                             'date': date_filter,
                             'room': room_filter
                         },
                         today=date.today)

@staff_bp.route('/checkin')
@staff_required
def staff_checkin():
    """Route untuk halaman checkin staff - menampilkan semua booking yang bisa dicheckin"""
    
    # Ambil booking yang sudah disetujui dan belum checkin (tanpa batasan hari)
    checkin_bookings = Booking.query\
        .join(User, Booking.user_id == User.id)\
        .join(Room, Booking.room_id == Room.id)\
        .filter(
            Booking.status == BookingStatus.APPROVED.value,
            Booking.checkin_time.is_(None)
        )\
        .add_columns(
            User.username,
            User.email,
            Room.name.label('room_name'),
            Room.capacity.label('room_capacity')
        )\
        .order_by(Booking.booking_date.desc(), Booking.start_time.asc()).all()
    
    # Ambil booking yang sudah checkin tapi belum checkout (sedang berlangsung)
    active_bookings = Booking.query\
        .join(User, Booking.user_id == User.id)\
        .join(Room, Booking.room_id == Room.id)\
        .filter(
            Booking.status == BookingStatus.CHECKED_IN.value,
            Booking.checkout_time.is_(None)
        )\
        .add_columns(
            User.username,
            User.email,
            Room.name.label('room_name'),
            Room.capacity.label('room_capacity')
        )\
        .order_by(Booking.booking_date.desc(), Booking.start_time.asc()).all()
    
    return render_template('staff/checkin.html',
                         checkin_bookings=checkin_bookings,
                         active_bookings=active_bookings)

@staff_bp.route('/checkout-booking/<int:booking_id>', methods=['POST'])
@staff_required
def checkout_booking(booking_id):
    """Route untuk checkout booking oleh staff"""
    try:
        # Ambil booking berdasarkan ID
        booking = Booking.query.get_or_404(booking_id)
        
        # Validasi apakah booking bisa di-checkout
        if not booking.can_checkout():
            return jsonify({
                'success': False,
                'message': 'Booking tidak dapat di-checkout. Pastikan booking sudah check-in dan belum checkout.'
            }), 400
        
        # Lakukan checkout
        from datetime import datetime
        checkout_time = datetime.now()
        booking.checkout_time = checkout_time
        booking.status = BookingStatus.COMPLETED.value
        
        # Cek apakah checkout terlambat
        expected_end = datetime.combine(booking.booking_date, booking.end_time)
        if checkout_time > expected_end:
            booking.is_late = True
            
            # Optional: Buat sanksi otomatis untuk keterlambatan
            # Anda bisa uncomment kode dibawah jika ingin membuat sanksi otomatis
            """
            late_minutes = booking.get_late_minutes()
            if late_minutes > 0:
                # Buat sanksi (misalnya Rp 1000 per menit keterlambatan)
                sanction_amount = late_minutes * 1000
                sanction = Sanction(
                    user_id=booking.user_id,
                    booking_id=booking.id,
                    amount=sanction_amount,
                    reason=f'Terlambat checkout {late_minutes} menit',
                    issued_by=current_user.id
                )
                db.session.add(sanction)
            """
        
        # Simpan perubahan
        db.session.commit()
        
        # Siapkan pesan response
        if booking.is_late:
            late_minutes = booking.get_late_minutes()
            message = f'Checkout berhasil! Perhatian: Terlambat {late_minutes} menit dari waktu yang dijadwalkan.'
        else:
            message = 'Checkout berhasil!'
        
        return jsonify({
            'success': True,
            'message': message,
            'checkout_time': checkout_time.strftime('%Y-%m-%d %H:%M:%S'),
            'is_late': booking.is_late
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'Terjadi kesalahan: {str(e)}'
        }), 500

@staff_bp.route('/checkin-booking/<int:booking_id>', methods=['POST'])
@staff_required
def checkin_booking(booking_id):
    """Route untuk checkin booking oleh staff"""
    try:
        # Ambil booking berdasarkan ID
        booking = Booking.query.get_or_404(booking_id)
        
        # Validasi apakah booking bisa di-checkin
        if not booking.can_checkin():
            return jsonify({
                'success': False,
                'message': 'Booking tidak dapat di-checkin. Pastikan booking sudah disetujui dan belum pernah checkin.'
            }), 400
        
        # Lakukan checkin
        booking.checkin_time = datetime.now()
        booking.status = BookingStatus.CHECKED_IN.value
        
        # Simpan perubahan
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Checkin berhasil!',
            'checkin_time': booking.checkin_time.strftime('%Y-%m-%d %H:%M:%S')
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'Terjadi kesalahan: {str(e)}'
        }), 500


