"""Operator-gated run: monitor each submission and retain driver for recovery.

Run with python -i so the portal object remains available after any failure.
Credentials are entered through the shared visible password prompt.
"""
import alexu_batch_add_course_registration as registration

portal = None
original_init = registration.PortalSession.__init__
original_submit = registration.PortalSession.submit_registration


def monitored_init(self, *args, **kwargs):
    global portal
    original_init(self, *args, **kwargs)
    portal = self


def monitored_submit(self):
    self.driver.save_screenshot('monitored_registration.png')
    input('[monitor] Course selected. Press Enter to submit this subject: ')
    result = original_submit(self)
    print(f'[monitor] Portal outcome: {result}', flush=True)
    return result


registration.PortalSession.__init__ = monitored_init
registration.PortalSession.submit_registration = monitored_submit
result = registration.main()
print(f'[monitor] Batch exit status: {result}; Chrome remains open.', flush=True)
