#ifndef PARTICLELOGOWIDGET_H
#define PARTICLELOGOWIDGET_H

#include <QColor>
#include <QImage>
#include <QPointF>
#include <QVector>
#include <QWidget>

class QTimer;

/**
 * @brief Animated hero widget: a field of brand-coloured particles that
 *        assemble the Animica logo, drift in a gentle constellation, and
 *        react to the pointer. Pure QPainter — no QML / OpenGL required so it
 *        builds and runs identically on the Linux / Windows / macOS bundles.
 *
 * The logo target points are sampled from the bundled :/icons/animica-wallet.png
 * alpha mask, so the particles literally re-form the app icon.
 */
class ParticleLogoWidget : public QWidget
{
    Q_OBJECT

public:
    explicit ParticleLogoWidget(QWidget* parent = nullptr);

    QSize sizeHint() const override;

protected:
    void paintEvent(QPaintEvent* event) override;
    void resizeEvent(QResizeEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;
    void leaveEvent(QEvent* event) override;
    void showEvent(QShowEvent* event) override;
    void hideEvent(QHideEvent* event) override;

private:
    struct Particle {
        QPointF pos;
        QPointF home;     // assembled (logo) target
        QColor color;
        double size = 1.5;
        double phase = 0.0;
        double orbit = 1.0;
        double speed = 0.02;
    };

    void rebuildTargets();
    void step();

    QVector<Particle> m_particles;
    QImage m_logo;
    QTimer* m_timer = nullptr;
    QPointF m_mouse;
    bool m_hasMouse = false;
    double m_t = 0.0;
    double m_assembly = 0.0;   // 0 → scattered, 1 → fully assembled
};

#endif // PARTICLELOGOWIDGET_H
