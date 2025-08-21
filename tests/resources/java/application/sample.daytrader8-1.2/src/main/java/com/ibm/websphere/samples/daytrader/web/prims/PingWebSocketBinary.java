/**
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package com.ibm.websphere.samples.daytrader.web.prims;

import java.io.IOException;
import java.nio.ByteBuffer;

import javax.websocket.CloseReason;
import javax.websocket.EndpointConfig;
import javax.websocket.OnClose;
import javax.websocket.OnError;
import javax.websocket.OnMessage;
import javax.websocket.OnOpen;
import javax.websocket.Session;
import javax.websocket.server.ServerEndpoint;

/** This class a simple websocket that echos the binary it has been sent. */

@ServerEndpoint(value = "/pingBinary")
public class PingWebSocketBinary {

    private Session currentSession = null;
   
    @OnOpen
    public void onOpen(final Session session, EndpointConfig ec) {
        currentSession = session;
    }

    @OnMessage
    public void ping(ByteBuffer data) {       
        currentSession.getAsyncRemote().sendBinary(data);
    }

    @OnError
    public void onError(Throwable t) {
        t.printStackTrace();
    }

    @OnClose
    public void onClose(Session session, CloseReason reason) {

        try {
            if (session.isOpen()) {
                session.close();
            }
        } catch (IOException e) {
            e.printStackTrace();
        }
    }

}
