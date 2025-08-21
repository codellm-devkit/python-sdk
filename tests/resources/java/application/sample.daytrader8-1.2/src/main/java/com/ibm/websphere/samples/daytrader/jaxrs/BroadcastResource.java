/**
 * (C) Copyright IBM Corporation 2019.
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
package com.ibm.websphere.samples.daytrader.jaxrs;

import java.util.List;

import javax.annotation.Priority;
import javax.enterprise.context.ApplicationScoped;
import javax.enterprise.event.ObservesAsync;
import javax.inject.Inject;
import javax.interceptor.Interceptor;
import javax.ws.rs.GET;
import javax.ws.rs.Path;
import javax.ws.rs.Produces;
import javax.ws.rs.core.Context;
import javax.ws.rs.core.MediaType;
import javax.ws.rs.sse.OutboundSseEvent.Builder;
import javax.ws.rs.sse.Sse;
import javax.ws.rs.sse.SseBroadcaster;
import javax.ws.rs.sse.SseEventSink;

import com.ibm.websphere.samples.daytrader.interfaces.QuotePriceChange;
import com.ibm.websphere.samples.daytrader.util.RecentQuotePriceChangeList;

@Path("broadcastevents")
@ApplicationScoped
public class BroadcastResource {

  private SseBroadcaster broadcaster;
  private Builder builder; 

  @Inject RecentQuotePriceChangeList recentQuotePriceChangeList;

  @Context
  public void setSse(Sse sse) {
    broadcaster = sse.newBroadcaster();
    builder = sse.newEventBuilder();
  }

  @GET
  @Produces(MediaType.SERVER_SENT_EVENTS)
  public void register(@Context SseEventSink eventSink) {
    if (recentQuotePriceChangeList.isEmpty()) {
      eventSink.send(builder.data(new String("welcome!")).build());
    } else {
      eventSink.send(builder.mediaType(MediaType.APPLICATION_JSON_TYPE)
      .data(List.class,recentQuotePriceChangeList.recentList()).build());
    }
    broadcaster.register(eventSink);
  }

  public void eventStreamCdi(@ObservesAsync @Priority(Interceptor.Priority.APPLICATION + 1) @QuotePriceChange String event) {
    broadcaster.broadcast(builder.mediaType(MediaType.APPLICATION_JSON_TYPE)
        .data(List.class,recentQuotePriceChangeList.recentList()).build());

  }
}
