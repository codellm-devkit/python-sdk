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
package com.ibm.websphere.samples.daytrader.web.websocket;

import javax.json.Json;
import javax.json.JsonObject;
import javax.websocket.EncodeException;
import javax.websocket.Encoder;
import javax.websocket.EndpointConfig;

public class JsonEncoder implements Encoder.Text<JsonMessage>{

    @Override
    public void destroy() {
    }

    @Override
    public void init(EndpointConfig ec) {
    }

    @Override
    public String encode(JsonMessage message) throws EncodeException {
        
        JsonObject jsonObject = Json.createObjectBuilder()
                .add("key", message.getKey())
                .add("value", message.getValue()).build();

        return jsonObject.toString();
    }

   

}
