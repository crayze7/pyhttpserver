import threading


# missing in python 3.7:
if hasattr(threading, 'get_native_id'):  # Python 3.8+
  def get_thread_id() -> int:
    return threading.get_native_id()
else:
  def get_thread_id() -> int:
    return threading.get_ident()
