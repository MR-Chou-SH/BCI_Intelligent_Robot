"""Thin newline-JSON planner and read-only world transports, no planning logic."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import socket
import socketserver
import threading
import time
import uuid

from robot_arm.kitchen.layouts import DEFAULT_PLATFORM_LAYOUT
from robot_arm.kitchen.platform import KitchenPlatform

MAX_REQUEST_BYTES=2*1024*1024


def encoded(value):
    return (json.dumps(value,separators=(",",":"),allow_nan=False)+"\n").encode("utf-8")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address=False
    daemon_threads=True


class PlannerHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(30.)
        self.writer_id=None
        self.connection_id=uuid.uuid4().hex
        try:self.read_requests()
        finally:self.server.platform.release_writer(self.writer_id,self.connection_id)

    def read_requests(self):
        while not self.server.stopping.is_set():
            try:
                line=self.rfile.readline(MAX_REQUEST_BYTES+1)
                if not line:return
                if len(line)>MAX_REQUEST_BYTES or not line.endswith(b"\n"):
                    self.wfile.write(encoded({"ok":False,"error":{"code":"REQUEST_TOO_LARGE_OR_INCOMPLETE"}}))
                    return
                try:
                    message=json.loads(line,parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                    if not isinstance(message,dict):raise ValueError("Expected JSON object")
                    writer_id=message.get("writer_id")
                    if self.writer_id is not None and writer_id is not None and self.writer_id!=writer_id:
                        raise ValueError("Writer identity cannot change on one connection")
                    response=self.server.platform.request(message.get("method"),message.get("args"),
                        request_id=message.get("request_id"),token=message.get("token"),writer_id=writer_id,connection_id=self.connection_id)
                    if response.get("ok") and message.get("method") in self.server.platform.mutation_methods:
                        self.writer_id=writer_id
                except (ValueError,TypeError,OverflowError) as error:
                    response={"ok":False,"error":{"code":"INVALID_JSON_REQUEST","detail":str(error)}}
                self.wfile.write(encoded(response))
                self.wfile.flush()
            except (OSError,TimeoutError):return


class WorldHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(2.)
        while not self.server.stopping.is_set():
            with self.server.platform.lock:
                frame=self.server.platform.world()
            try:
                # Finish this entire frame before taking the next snapshot.
                # A stalled peer is disconnected; no in-flight TCP tail is dropped.
                self.request.sendall(encoded(frame))
            except OSError:return
            self.server.stopping.wait(.04)


def serve(args):
    platform=KitchenPlatform(layout=args.layout,seed=args.seed,mode=args.mode,data_root=args.data_root,
        initialization_profile=args.initialization_profile,record_physics=getattr(args,"diagnostics",False),
        evidence_compression="gzip" if getattr(args,"diagnostics",False) else None)
    stopping=threading.Event()
    planner=world=None
    workers=[]
    started_servers=[]
    def stop(*_):stopping.set()
    signal.signal(signal.SIGINT,stop)
    signal.signal(signal.SIGTERM,stop)
    started=time.monotonic()
    try:
        planner=Server((args.bind_host,args.planner_port),PlannerHandler)
        world=Server((args.bind_host,args.world_port),WorldHandler)
        for server in (planner,world):
            server.platform=platform
            server.stopping=stopping
            worker=threading.Thread(target=server.serve_forever,kwargs={"poll_interval":.1},daemon=True)
            worker.start();workers.append(worker)
            started_servers.append(server)
        ready={"status":"PLATFORM_READY","planner_host":args.bind_host,
               "planner_port":planner.server_address[1],"world_port":world.server_address[1],
               "layout":args.layout,"seed":platform.episode["seed"],"mode":args.mode,"data_root":str(args.data_root.resolve()),
               "initialization_profile":args.initialization_profile,"episode":platform.episode,
               "import_root":str(Path(__file__).resolve().parents[2]),"token":platform.token(),
               "quest":"HARDWARE_REQUIRED","formal_layout":"AWAITING_USER_REVIEW"}
        args.ready_file.parent.mkdir(parents=True,exist_ok=True)
        with args.ready_file.open("x",encoding="utf-8") as file:json.dump(ready,file,indent=2)
        print(json.dumps(ready),flush=True)
        next_step=time.monotonic()
        while not stopping.wait(.002):
            if args.run_seconds and time.monotonic()-started>=args.run_seconds:break
            now=time.monotonic()
            if args.mode=="realtime" and now>=next_step:
                with platform.lock:platform.advance(20)
                # No unbounded catch-up or second wall-clock physics owner.
                next_step=time.monotonic()+.04
    finally:
        stopping.set()
        for server in (planner,world):
            if server is not None:
                if server in started_servers:server.shutdown()
                server.server_close()
        platform.close()
        for worker in workers:worker.join(2.)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--layout",default=DEFAULT_PLATFORM_LAYOUT)
    parser.add_argument("--seed",type=int,default=None)
    parser.add_argument("--initialization-profile",choices=("fixed","research"),default="fixed")
    parser.add_argument("--mode",choices=("manual","realtime"),default="manual")
    parser.add_argument("--data-root",type=Path,required=True)
    parser.add_argument("--ready-file",type=Path,required=True)
    parser.add_argument("--bind-host",default="127.0.0.1")
    parser.add_argument("--planner-port",type=int,default=11031)
    parser.add_argument("--world-port",type=int,default=11032)
    parser.add_argument("--run-seconds",type=float,default=0)
    args=parser.parse_args()
    if any(not 0<=port<=65535 for port in (args.planner_port,args.world_port)):
        parser.error("Ports must be 0..65535; 0 selects an available port")
    if args.planner_port and args.planner_port==args.world_port:parser.error("Distinct ports required")
    if not 0<=args.run_seconds<=3600:parser.error("run-seconds must be 0..3600")
    serve(args)


if __name__=="__main__":main()
