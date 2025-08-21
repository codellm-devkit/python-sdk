//
// COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
// modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
// developing, using, marketing or distributing application programs conforming to the application 
// programming interface for the operating platform for which the sample code is written. 
// Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
// AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
// WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
// TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
// INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
// SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
// OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  
//
// (C) COPYRIGHT International Business Machines Corp., 2004,2011
// All Rights Reserved * Licensed Materials - Property of IBM
//
package com.ibm.websphere.samples.pbw.jpa;

import javax.persistence.Entity;
import javax.persistence.Id;
import javax.persistence.NamedQueries;
import javax.persistence.NamedQuery;
import javax.persistence.Table;

/**
 * Bean mapping for the SUPPLIER table.
 */
@Entity(name = "Supplier")
@Table(name = "SUPPLIER", schema = "APP")
@NamedQueries({ @NamedQuery(name = "findAllSuppliers", query = "select s from Supplier s"),
		@NamedQuery(name = "removeAllSupplier", query = "delete from Supplier") })
public class Supplier {
	@Id
	private String supplierID;
	private String name;
	private String city;
	private String usstate;
	private String zip;
	private String phone;
	private String url;
	private String street;

	public String getCity() {
		return city;
	}

	public void setCity(String city) {
		this.city = city;
	}

	public String getName() {
		return name;
	}

	public void setName(String name) {
		this.name = name;
	}

	public String getPhone() {
		return phone;
	}

	public void setPhone(String phone) {
		this.phone = phone;
	}

	public String getStreet() {
		return street;
	}

	public void setStreet(String street) {
		this.street = street;
	}

	public String getSupplierID() {
		return supplierID;
	}

	public void setSupplierID(String supplierID) {
		this.supplierID = supplierID;
	}

	public String getUrl() {
		return url;
	}

	public void setUrl(String url) {
		this.url = url;
	}

	public String getUsstate() {
		return usstate;
	}

	public void setUsstate(String usstate) {
		this.usstate = usstate;
	}

	public String getZip() {
		return zip;
	}

	public void setZip(String zip) {
		this.zip = zip;
	}

	public Supplier() {
	}

	public Supplier(String supplierID) {
		setSupplierID(supplierID);
	}

	/**
	 * @param supplierID
	 * @param name
	 * @param street
	 * @param city
	 * @param state
	 * @param zip
	 * @param phone
	 * @param url
	 */
	public Supplier(String supplierID, String name, String street, String city, String state, String zip, String phone,
			String url) {
		this.setSupplierID(supplierID);
		this.setName(name);
		this.setStreet(street);
		this.setCity(city);
		this.setUsstate(state);
		this.setZip(zip);
		this.setPhone(phone);
		this.setUrl(url);
	}
}
