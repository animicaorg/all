#include "ParticleLogoWidget.h"

#include <QLinearGradient>
#include <QMouseEvent>
#include <QPainter>
#include <QPainterPath>
#include <QRadialGradient>
#include <QRandomGenerator>
#include <QTimer>
#include <QtMath>

#include <cmath>

namespace {
// Animica brand palette.
const QColor kTeal(0x16, 0xd9, 0xc3);
const QColor kBlue(0x3a, 0x9b, 0xff);
const QColor kMint(0x6f, 0xff, 0xd6);
const QColor kBgTop(0x07, 0x0d, 0x1c);
const QColor kBgBottom(0x04, 0x06, 0x10);

QColor lerpColor(const QColor& a, const QColor& b, double t)
{
    t = qBound(0.0, t, 1.0);
    return QColor::fromRgbF(
        a.redF() + (b.redF() - a.redF()) * t,
        a.greenF() + (b.greenF() - a.greenF()) * t,
        a.blueF() + (b.blueF() - a.blueF()) * t);
}
} // namespace

ParticleLogoWidget::ParticleLogoWidget(QWidget* parent)
    : QWidget(parent)
{
    setMinimumHeight(200);
    setMouseTracking(true);
    setAttribute(Qt::WA_OpaquePaintEvent, true);

    m_logo = QImage(QStringLiteral(":/icons/animica-wallet.png"));
    if (!m_logo.isNull()) {
        m_logo = m_logo.convertToFormat(QImage::Format_ARGB32);
    }

    m_timer = new QTimer(this);
    m_timer->setInterval(16); // ~60 fps
    connect(m_timer, &QTimer::timeout, this, [this]() {
        step();
        update();
    });
}

QSize ParticleLogoWidget::sizeHint() const
{
    return QSize(900, 240);
}

void ParticleLogoWidget::showEvent(QShowEvent* event)
{
    QWidget::showEvent(event);
    if (m_particles.isEmpty()) {
        rebuildTargets();
    }
    if (m_timer && !m_timer->isActive()) {
        m_timer->start();
    }
}

void ParticleLogoWidget::hideEvent(QHideEvent* event)
{
    QWidget::hideEvent(event);
    if (m_timer) {
        m_timer->stop(); // don't burn CPU while the tab is hidden
    }
}

void ParticleLogoWidget::resizeEvent(QResizeEvent* event)
{
    QWidget::resizeEvent(event);
    rebuildTargets();
}

void ParticleLogoWidget::mouseMoveEvent(QMouseEvent* event)
{
    m_mouse = event->position();
    m_hasMouse = true;
    QWidget::mouseMoveEvent(event);
}

void ParticleLogoWidget::leaveEvent(QEvent* event)
{
    m_hasMouse = false;
    QWidget::leaveEvent(event);
}

void ParticleLogoWidget::rebuildTargets()
{
    const int w = width();
    const int h = height();
    if (w <= 0 || h <= 0) {
        return;
    }

    QRandomGenerator* rng = QRandomGenerator::global();

    // Collect logo target points by sampling the alpha mask of the icon.
    QVector<QPointF> targets;
    QVector<QColor> colors;
    if (!m_logo.isNull()) {
        const double logoH = qMin<double>(h * 0.66, w * 0.34);
        const double scale = logoH / m_logo.height();
        const double logoW = m_logo.width() * scale;
        const double ox = (w - logoW) / 2.0;
        const double oy = (h - logoH) / 2.0;

        // Aim for ~620 particles regardless of widget size.
        const int want = 620;
        const double area = logoW * logoH;
        int stride = qMax(2, static_cast<int>(qSqrt(area / qMax(1, want)) / scale));

        for (int sy = 0; sy < m_logo.height(); sy += stride) {
            for (int sx = 0; sx < m_logo.width(); sx += stride) {
                if (qAlpha(m_logo.pixel(sx, sy)) < 110) {
                    continue;
                }
                const double px = ox + sx * scale;
                const double py = oy + sy * scale;
                targets.append(QPointF(px, py));
                const double tx = (sx / double(m_logo.width()));
                QColor c = lerpColor(kTeal, kBlue, 0.25 + tx * 0.6);
                if (rng->bounded(100) < 14) {
                    c = lerpColor(c, kMint, 0.6); // sparkle highlights
                }
                colors.append(c);
            }
        }
    }

    if (targets.isEmpty()) {
        // Fallback: a glowing ring if the icon could not be loaded.
        const int n = 480;
        const QPointF c(w / 2.0, h / 2.0);
        const double r = qMin(w, h) * 0.3;
        for (int i = 0; i < n; ++i) {
            const double a = (i / double(n)) * 2 * M_PI;
            targets.append(c + QPointF(qCos(a) * r, qSin(a) * r));
            colors.append(lerpColor(kTeal, kBlue, (qSin(a) + 1) / 2.0));
        }
    }

    const int n = targets.size();
    m_particles.resize(n);
    for (int i = 0; i < n; ++i) {
        Particle& p = m_particles[i];
        p.home = targets[i];
        p.color = colors[i];
        // Start scattered around the widget for the assemble-in effect.
        if (p.pos.isNull()) {
            p.pos = QPointF(rng->bounded(w), rng->bounded(h));
        }
        p.size = 1.1 + rng->bounded(180) / 100.0;
        p.phase = rng->bounded(628) / 100.0;
        p.orbit = 0.8 + rng->bounded(160) / 100.0;
        p.speed = 0.012 + rng->bounded(20) / 1000.0;
    }
}

void ParticleLogoWidget::step()
{
    m_t += 0.016;
    if (m_assembly < 1.0) {
        m_assembly = qMin(1.0, m_assembly + 0.010);
    }

    const double ease = 0.04 + 0.05 * m_assembly;
    const double repelR = 90.0;

    for (Particle& p : m_particles) {
        p.phase += p.speed;

        // Gentle orbital drift around the assembled home position.
        const double drift = (1.0 - m_assembly) * 60.0;
        QPointF orbit(qCos(p.phase) * p.orbit, qSin(p.phase * 1.3) * p.orbit);
        QPointF desired = p.home + orbit
            + QPointF(qCos(p.phase * 0.5) * drift, qSin(p.phase * 0.7) * drift);

        p.pos += (desired - p.pos) * ease;

        // Pointer repel for an interactive feel.
        if (m_hasMouse) {
            QPointF d = p.pos - m_mouse;
            const double dist = std::hypot(d.x(), d.y());
            if (dist < repelR && dist > 0.001) {
                const double force = (1.0 - dist / repelR) * 6.0;
                p.pos += (d / dist) * force;
            }
        }
    }
}

void ParticleLogoWidget::paintEvent(QPaintEvent*)
{
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing, true);

    // Background gradient.
    QLinearGradient bg(0, 0, 0, height());
    bg.setColorAt(0.0, kBgTop);
    bg.setColorAt(1.0, kBgBottom);
    painter.fillRect(rect(), bg);

    // Soft brand glow behind the logo.
    QRadialGradient halo(rect().center(), qMax(width(), height()) * 0.5);
    halo.setColorAt(0.0, QColor(0x16, 0xd9, 0xc3, 26));
    halo.setColorAt(1.0, QColor(0x16, 0xd9, 0xc3, 0));
    painter.fillRect(rect(), halo);

    // Faint constellation links between neighbouring particles (cheap: i↔i+1).
    painter.setCompositionMode(QPainter::CompositionMode_Plus);
    const int n = m_particles.size();
    for (int i = 0; i + 1 < n; ++i) {
        const QPointF& a = m_particles[i].pos;
        const QPointF& b = m_particles[i + 1].pos;
        const double dx = a.x() - b.x();
        const double dy = a.y() - b.y();
        const double d2 = dx * dx + dy * dy;
        if (d2 < 26.0 * 26.0) {
            const double alpha = (1.0 - d2 / (26.0 * 26.0)) * 36.0 * m_assembly;
            QColor lc = m_particles[i].color;
            lc.setAlpha(static_cast<int>(qBound(0.0, alpha, 80.0)));
            painter.setPen(QPen(lc, 0.6));
            painter.drawLine(a, b);
        }
    }

    // Particles: a soft halo + a bright core, additively blended.
    painter.setPen(Qt::NoPen);
    for (const Particle& p : m_particles) {
        const double s = p.size * (0.7 + 0.3 * (0.5 + 0.5 * qSin(p.phase * 2.0)));

        QColor halc = p.color;
        halc.setAlpha(60);
        painter.setBrush(halc);
        painter.drawEllipse(p.pos, s * 3.0, s * 3.0);

        QColor core = lerpColor(p.color, QColor(Qt::white), 0.35);
        core.setAlpha(230);
        painter.setBrush(core);
        painter.drawEllipse(p.pos, s, s);
    }
    painter.setCompositionMode(QPainter::CompositionMode_SourceOver);

    // Bottom fade so overlaid text stays legible.
    QLinearGradient fade(0, height() * 0.45, 0, height());
    fade.setColorAt(0.0, QColor(4, 6, 16, 0));
    fade.setColorAt(1.0, QColor(4, 6, 16, 210));
    painter.fillRect(rect(), fade);
}
